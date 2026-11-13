import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

PRELOAD = Path(__file__).resolve().parents[1] / "linuxfs/preload"

PROGRAM = r"""
#define _GNU_SOURCE
#include <dlfcn.h>
#include <fcntl.h>
#include <pthread.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

static const char *ctl;
static size_t size, page;
static volatile int stop;
static unsigned char *shared;

static long status(const char *key) {
  FILE *f = fopen("/proc/self/status", "r");
  char line[256];
  long v = -1;
  while (f && fgets(line, sizeof(line), f))
    if (!strncmp(line, key, strlen(key))) v = atol(line + strlen(key));
  if (f) fclose(f);
  return v;
}

static void request(char mode) {
  int fd = open(ctl, O_WRONLY | O_TRUNC);
  if (fd < 0 || write(fd, &mode, 1) != 1) exit(3);
  close(fd);
  kill(getpid(), SIGURG);
}

static uint64_t sum(const unsigned char *p, size_t n) {
  uint64_t s = 0;
  for (size_t i = 0; i < n; i += 64) s = s * 31 + p[i];
  return s;
}

static void *writer(void *arg) {
  unsigned char *mine = shared + (size_t)(uintptr_t)arg * (size / 4);
  for (unsigned char round = 1; !stop; round++) {
    for (size_t i = 0; i < size / 4; i += page) {
      if (round > 1 && mine[i] != (unsigned char)(round - 1)) {
        printf("corrupt\n");
        exit(4);
      }
      mine[i] = round;
    }
  }
  return NULL;
}

int main(int argc, char **argv) {
  void (*init)(void) = dlsym(RTLD_DEFAULT, "zr_test_init");
  if (init) init();
  ctl = argv[1];
  size = strtoul(argv[2], NULL, 0) << 20;
  int cycles = atoi(argv[3]);
  page = (size_t)sysconf(_SC_PAGESIZE);
  unsigned char *data = mmap(NULL, size, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
  for (size_t i = 0; i < size; i++) data[i] = (unsigned char)(i * 7 + i / 4096);
  uint64_t before = sum(data, size);
  shared = mmap(NULL, size, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
  memset(shared, 0, size);
  pthread_t t[2];
  for (uintptr_t i = 0; i < 2; i++) pthread_create(&t[i], NULL, writer, (void *)i);
  struct sigaction sa;
  sigaction(SIGURG, NULL, &sa);
  printf("handler %d\n", (sa.sa_flags & SA_SIGINFO) ? 1 : 0);
  if (argc > 4) {
    unsigned char *ro = mmap(NULL, size, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    memset(ro, 0x5a, size);
    mprotect(ro, size, PROT_READ);
    madvise(ro, size, 21);
    printf("elsewhere %ld\n", status("VmSwap:"));
    request('0');
    printf("back %ld\n", status("VmSwap:"));
  }
  for (int c = 0; c < cycles; c++) {
    request('1');
    printf("out %ld\n", status("VmSwap:"));
    request('0');
    printf("back %ld\n", status("VmSwap:"));
  }
  stop = 1;
  for (int i = 0; i < 2; i++) pthread_join(t[i], NULL);
  printf("intact %d\n", sum(data, size) == before);
  return 0;
}
"""


def swap_total_kb():
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("SwapTotal:"):
            return int(line.split()[1])
    return 0


@unittest.skipUnless(shutil.which("cc"), "needs a C compiler")
class ZramPreloadTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = Path(tempfile.mkdtemp())
        cls.ctl = cls.dir / "ctl"
        build = cls.dir / "build"
        build.mkdir()
        (build / "program.c").write_text(PROGRAM)
        (build / "zram-test.c").write_text(
            '#include "' + str(PRELOAD / 'zram.c') + '"\n'
            'void zr_test_init(void) {\n'
            '  struct sigaction sa = {.sa_handler = SIG_DFL};\n'
            '  sigemptyset(&sa.sa_mask);\n'
            '  sigaction(SIGURG, &sa, NULL);\n'
            '  zr_init();\n'
            '}\n')
        cls.lib = build / "libblsession.so"
        subprocess.run(["cc", "-shared", "-fPIC", "-O2", "-Wall", "-pthread", "-DZR_CTL=\"%s\"" % cls.ctl,
                        "-o", str(cls.lib), str(build / "zram-test.c"),
                        *map(str, sorted(p for p in PRELOAD.glob("*.c") if p.name != 'zram.c')), "-ldl"], check=True)
        cls.steam = cls.dir / "steam"
        subprocess.run(["cc", "-O2", "-pthread", "-o", str(cls.steam), str(build / "program.c"), "-ldl"], check=True)
        cls.game = cls.dir / "game"
        shutil.copy(cls.steam, cls.game)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, True)

    def setUp(self):
        self.ctl.write_text("0")
        for ack in self.dir.glob("ctl.ack.*"):
            ack.unlink()

    def run_program(self, program=None, mb=48, cycles=1, *extra):
        environment = dict(os.environ, LD_PRELOAD=str(self.lib))
        result = subprocess.run([str(program or self.steam), str(self.ctl), str(mb), str(cycles), *extra], env=environment,
                                capture_output=True, text=True, check=True, timeout=120)
        out = {}
        for line in result.stdout.splitlines():
            key, value = line.split()
            out.setdefault(key, []).append(int(value))
        return out

    def test_every_process_takes_the_signal(self):
        self.assertEqual([1], self.run_program()["handler"])
        self.assertEqual([1], self.run_program(self.game)["handler"])
        self.assertEqual(2, len(list(self.dir.glob("ctl.ack.*"))))

    def test_nothing_is_installed_without_the_control_file(self):
        self.ctl.unlink()
        try:
            self.assertEqual([0], self.run_program(cycles=0)["handler"])
        finally:
            self.ctl.write_text("0")

    @unittest.skipUnless(swap_total_kb() > 256 * 1024, "needs swap")
    def test_pages_out_and_reads_back(self):
        out = self.run_program(mb=48)
        self.assertGreater(out["out"][0], 32 * 1024)
        self.assertLess(out["back"][0], out["out"][0] // 4)
        self.assertEqual([1], out["intact"])
        self.assertEqual(["1"], [ack.read_text() for ack in self.dir.glob("ctl.ack.*")])

    @unittest.skipUnless(swap_total_kb() > 256 * 1024, "needs swap")
    def test_reads_back_what_the_kernel_swapped_elsewhere(self):
        out = self.run_program(None, 32, 0, "read-only")
        self.assertGreater(out["elsewhere"][0], 16 * 1024)
        self.assertLess(out["back"][0], out["elsewhere"][0] // 4)

    def test_memory_stays_intact_under_writers(self):
        out = self.run_program(mb=32, cycles=6)
        self.assertEqual([1], out["intact"])
        self.assertEqual(6, len(out["out"]))


@unittest.skipUnless(shutil.which("cc"), "needs a C compiler")
class NativeZramControlTest(unittest.TestCase):
    def test_native_control_file_and_acknowledgement_use_host_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            virtual = '/tmp/' + folder.name + '-native-zram'
            root = folder / 'guest'
            ctl = root / virtual.lstrip('/')
            ctl.parent.mkdir(parents=True)
            ctl.write_text('1')
            self.check_control(folder, virtual, str(root) + '/', ctl, True)

    def test_already_mapped_control_path_is_not_prefixed_twice(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            root = folder / 'guest'
            root.mkdir()
            ctl = root / 'ctl'
            ctl.write_text('1')
            self.check_control(folder, str(ctl), str(root), ctl, True)

    def test_native_mode_without_its_control_file_installs_no_handler(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            self.check_control(folder, '/tmp/' + folder.name + '-missing',
                               str(folder / 'guest'), folder / 'missing', False)

    def check_control(self, folder, virtual, root, ctl, enabled):
        source = folder / 'probe.c'
        source.write_text(
            '#define ZR_CTL "' + virtual + '"\n'
            '#include "' + str(PRELOAD / 'zram.c') + '"\n'
            '#include <stdio.h>\n'
            'int main(int argc, char **argv) {\n'
            '  (void)argc;\n'
            '  setenv("DDNATV_ROOT", argv[1], 1);\n'
            '  struct sigaction reset = {.sa_handler = SIG_DFL};\n'
            '  sigemptyset(&reset.sa_mask);\n'
            '  sigaction(SIGURG, &reset, NULL);\n'
            '  zr_init();\n'
            '  struct sigaction sa;\n'
            '  sigaction(SIGURG, NULL, &sa);\n'
            '  int installed = !!(sa.sa_flags & SA_SIGINFO);\n'
            '  printf("%d\\n", installed);\n'
            '  if (installed) kill(getpid(), SIGURG);\n'
            '  return 0;\n'
            '}\n')
        program = folder / 'probe'
        subprocess.run(['cc', '-O2', '-Wall', '-Wextra', '-Werror', '-pthread',
                        '-o', str(program), str(source), '-ldl'], check=True)
        result = subprocess.run([str(program), root], capture_output=True, text=True,
                                check=True, timeout=15)
        self.assertEqual(str(int(enabled)), result.stdout.strip(), result.stderr)
        acks = list(ctl.parent.glob(ctl.name + '.ack.*'))
        self.assertEqual(['1'] if enabled else [], [p.read_text() for p in acks])


if __name__ == "__main__":
    unittest.main()
