"""sage-runtime: switching docker between Colima, Docker Desktop and OrbStack, and copying data across."""

import hashlib
import json
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parent.parent / "sage-runtime"
FORMULA = TOOL.parent / "Formula" / "sage-runtime.rb"
# krunkit's dependencies come from its tap too, and Homebrew 7 refuses each one from an untrusted tap.
KRUNKIT_TAP = "brew tap libkrun/krun && brew trust libkrun/krun"
KRUNKIT_INSTALL = f"{KRUNKIT_TAP} && brew install krunkit"
# What a krunkit VM needs in Lima's override file: virtiofs (abiosoft/colima#1607),
# and a boot step that mounts its data disk (abiosoft/colima#1614).
VIRTIOFS = ("# Colima 0.10.3 hands Lima 9p for krunkit, and Lima 2.2 refuses it: abiosoft/colima#1607\n"
            "mountType: virtiofs\n")
DATA_DISK_STEP = """\
  - mode: dependency
    script: |
      #!/bin/sh
      for link in /dev/disk/by-label/lima-*; do
        [ -e "$link" ] || continue
        dir="/mnt/${link##*/}"
        mountpoint -q "$dir" || { mkdir -p "$dir" && mount "$link" "$dir"; }
      done
"""
DATA_DISK = ("# krunkit puts the data disk on vdc, where Lima 2.2 looks for vdb; mount it by label: abiosoft/colima#1614\n"
             "provision:\n" + DATA_DISK_STEP)

# Stand-ins log each call. Each docker context has a running marker and a
# folder per volume under $STATE/vol-<context>/. A $STATE/fail-* file makes
# that step fail, as a busy or broken daemon would; $STATE/hang-<context>
# makes a running engine take every question and never answer. Docker in a
# Colima VM whose data disk went unmounted ($STATE/root-disk-<context>) sees
# its volumes, images and containers under $STATE/rootfs instead: none.
STUBS = {
    # Like docker: --context, else DOCKER_HOST (the default context), else
    # DOCKER_CONTEXT, else the saved context.
    "docker": r"""ctx="$(cat "$STATE/current-context" 2>/dev/null || echo default)"
[[ -z "${DOCKER_CONTEXT:-}" ]] || ctx="$DOCKER_CONTEXT"
[[ -z "${DOCKER_HOST:-}" ]] || ctx=default
if [[ "${1:-}" == --context ]]; then ctx="$2"; shift 2; fi
echo "docker $ctx $*" >> "$STATE/calls"
disk="$STATE"; [[ ! -e "$STATE/root-disk-$ctx" ]] || disk="$STATE/rootfs"
vols="$disk/vol-$ctx"
if [[ -e "$STATE/running-$ctx" && -e "$STATE/hang-$ctx" && "$1" != context && "$1" != buildx ]]; then exec sleep 60; fi
# A Colima VM has its context while it runs; an app has its own once installed.
contexts() {
  echo default; echo desktop-linux
  [[ ! -d "$APPLICATIONS_DIR/OrbStack.app" ]] || echo orbstack
  for m in "$STATE"/running-colima*; do if [[ -e "$m" ]]; then echo "${m##*/running-}"; fi; done
}
case "$1 ${2:-}" in
  # Docker's root folder comes from $STATE/data-root (data-root in its settings).
  "info "*)        [[ -e "$STATE/running-$ctx" && ! -e "$STATE/fail-info-$ctx" ]] || exit 1
                   [[ "${2:-}" != --format ]] || cat "$STATE/data-root" 2>/dev/null || echo /var/lib/docker ;;
  "context show")  echo "$ctx" ;;
  "context use")   contexts | grep -qxF "$3" || { echo "context \"$3\": context not found" >&2; exit 1; }
                   echo "$3" > "$STATE/current-context"; echo "$3"; echo "Current context is now \"$3\"" >&2 ;;
  # Removing the current context, judged by the environment first, leaves the default.
  "context rm")    [[ "$ctx" != "${@: -1}" ]] || echo default > "$STATE/current-context" ;;
  "context ls")    contexts ;;
  "context inspect") contexts | grep -qxF "$3" ;;
  "volume ls")     [[ ! -e "$STATE/fail-volume-ls-$ctx" ]] || exit 1; ls "$vols" 2>/dev/null || true ;;
  # With --format, each volume's name and the folder it is bound to, from
  # $STATE/device-<context>/<name>; none for most.
  "volume inspect") if [[ "$3" != --format ]]; then [[ -d "$vols/$3" ]]; exit; fi
                   for n in "${@:5}"; do echo "$n $(cat "$STATE/device-$ctx/$n" 2>/dev/null)"; done ;;
  "buildx version") [[ ! -e "$STATE/no-buildx" ]] ;;
  "ps "*)
    [[ ! -e "$STATE/fail-ps-$ctx" ]] || exit 1
    for f in "$disk/run-$ctx"/* "$disk/stopped-$ctx"/*; do
      [[ -e "$f" ]] || continue
      [[ "$f" != */stopped-* || "$2" == -a ]] || continue
      mounts=",$(tr -d '\n' < "$f" | tr ' ' ',')"
      # Docker Desktop lists a bind source as its VM sees it.
      [[ "$ctx" != desktop-linux ]] || mounts="${mounts//,\//,/host_mnt/}"
      printf '%s\t%s\n' "${f##*/}" "${mounts#,}"
    done ;;
  "save -o")
    # The daemon refuses a repo:<none> reference; $STATE/unsavable lists more.
    if [[ "$4" == *"<none>"* ]] || grep -qxF "$4" "$STATE/unsavable" 2>/dev/null; then
      echo "invalid reference format" >&2; exit 1
    fi
    printf 'IMAGE %s' "$4" > "$3" ;;
  "load -i")       data="$(cat "$3")"; printf '%s' "$data" >> "$STATE/loaded-$ctx"
                   printf '%s\t1MB\n' "${data#IMAGE }" >> "$disk/images-$ctx" ;;
  "volume rm")     rm -rf "$vols/${@: -1}" ;;
  "images --filter") cat "$disk/images-$ctx" 2>/dev/null || true ;;
  "images --all")  # An image's ID: the sha256 of its name, here.
                   cut -f1 "$disk/images-$ctx" 2>/dev/null | while read -r ref; do
                     echo "sha256:$(printf '%s' "$ref" | shasum -a 256 | cut -d' ' -f1)"
                   done ;;
  # Restart policies come from $STATE/policy-<context>/<name>; always when unset.
  # A container started with --rm has $STATE/rm-<context>/<name>.
  "inspect --format") format="$3"; shift 3
                   for n in "$@"; do
                     line="/$n $(cat "$STATE/policy-$ctx/$n" 2>/dev/null || echo always)"
                     if [[ "$format" == *AutoRemove* ]]; then
                       if [[ -e "$STATE/rm-$ctx/$n" ]]; then line+=" true"; else line+=" false"; fi
                     fi
                     echo "$line"
                   done ;;
  "start "*)       [[ ! -e "$STATE/unstartable" ]] || exit 1
                   if [[ -e "$disk/stopped-$ctx/$2" ]]; then
                     mkdir -p "$disk/run-$ctx"; mv "$disk/stopped-$ctx/$2" "$disk/run-$ctx/"
                   fi ;;
  "image inspect")
    size="$(awk -F '\t' -v ref="${@: -1}" '$1 == ref {print $2; exit}' "$disk/images-$ctx" 2>/dev/null)"
    [[ -n "$size" ]] || exit 1
    # The listing gives sizes such as 369MB; --format '{{.Size}}' gives bytes.
    if [[ "$3" == --format ]]; then
      awk -v s="$size" 'BEGIN {n = s + 0; u = s; sub(/^[0-9.]+/, "", u); printf "%d\n", n * (u == "GB" ? 1e9 : u == "MB" ? 1e6 : u == "kB" ? 1e3 : 1)}'
    fi ;;
  "run "*)
    # -v NAME:... is the volume; -v /PATH:/stage is the staging folder.
    vol="" stage="" prev=""
    for a in "$@"; do
      if [[ "$prev" == -v ]]; then
        if [[ "$a" == /* ]]; then stage="${a%%:*}"; else vol="${a%%:*}"; fi
      fi
      prev="$a"
    done
    dir="$vols/$vol"; mkdir -p "$dir"
    last="${*: -1}"
    if [[ "$*" == *" df -Pk /v" ]]; then
      printf 'Filesystem 1024-blocks Used Available Capacity Mounted on\n/dev/vdb1 209715200 0 %s 0%% /v\n' \
        "${VM_FREE_KIB:-104857600}"
    elif [[ "$*" == *" -cf /stage/"* ]]; then
      [[ ! -e "$STATE/fail-cf" ]] || exit 1
      all="$*"; f="${all##* -cf /stage/}"; tar -C "$dir" -cf "$stage/${f%% *}" .
    elif [[ "$*" == *" -xf /stage/"* ]]; then
      tar -C "$dir" -xf "$stage/${last#/stage/}"
      if [[ -e "$STATE/hold-xf" ]]; then  # mid-copy until the test lets go
        touch "$STATE/extracting"
        for _ in $(seq 200); do [[ ! -e "$STATE/release" ]] || break; sleep 0.05; done
      fi
      [[ ! -e "$STATE/fail-xf" ]] || exit 1
    elif [[ "$*" == *" find /v -mindepth 1 -delete"* ]]; then
      # A clear that fails partway: one entry goes, then it stops.
      if [[ -e "$STATE/fail-clear" ]]; then
        rm "$STATE/fail-clear"
        first="$(ls "$dir" | head -1)"; [[ -z "$first" ]] || rm -rf "${dir:?}/$first"
        exit 1
      fi
      find "$dir" -mindepth 1 -delete
    elif [[ "$*" == *" sh -c "* ]]; then
      [[ ! -e "$STATE/fail-probe-$ctx" ]] || exit 1
      sh -c "${last//\/v/$dir}"
    fi ;;
esac""",
    # One VM per profile, marked $STATE/vm-<profile>; it runs while its
    # context's running marker is there. As in Colima, COLIMA_PROFILE picks
    # the VM a bare command acts on, a start makes the VM's context docker's
    # current one unless the VM was started once with --activate=false, and a
    # stop removes the context with docker itself. Every command makes
    # Colima's home and Lima's in it, found as Colima finds them; a new VM
    # gets Lima's file with its type, its size ($STATE/size-<profile>: CPUs,
    # GiB of memory and of disk), its mounts, one per line in
    # $STATE/mounts-<profile>, and a data disk in Lima's _disks, unless it
    # is $STATE/one-disk. A krunkit VM comes back with Docker on its root
    # disk, $STATE/root-disk-<context>, on any boot of a data disk used
    # before, unless Lima's override mounts it by label (abiosoft/colima#1614),
    # and on every boot with $STATE/fail-mount. A stop stops the containers
    # whose restart policy will not bring them back, and Docker removes those
    # started with --rm, with their anonymous volumes. delete keeps the data
    # disk, and Docker's data on it, unless told --data; it takes the VM's
    # Colima settings. ssh runs the command as if inside the VM, and reads
    # stdin to pass it on, as ssh does; $STATE/fail-ssh-krunkit fails it into
    # a krunkit VM. $STATE/fail-start-krunkit fails a new krunkit VM.
    "colima": r"""echo "colima $*" >> "$STATE/calls"
profile="${COLIMA_PROFILE:-default}" prev=""
for a in "$@"; do [[ "$prev" == --profile ]] && profile="$a"; prev="$a"; done
ctx=colima; [[ "$profile" == default ]] || ctx="colima-$profile"
home="$HOME/.colima"
[[ -e "$home" || -z "${XDG_CONFIG_HOME:-}" ]] || home="$XDG_CONFIG_HOME/colima"
[[ -z "${COLIMA_HOME:-}" || ! -e "$COLIMA_HOME" ]] || home="$COLIMA_HOME"
lima="${LIMA_HOME:-$home/_lima}"
mkdir -p "$lima"
datadisk="$lima/_disks/$ctx/datadisk"
data="$STATE"; [[ ! -e "$STATE/root-disk-$ctx" ]] || data="$STATE/rootfs"
krunkit() { grep -q '^vmType: krunkit' "$lima/$ctx/lima.yaml" 2>/dev/null; }
case "$1" in
  list)  for m in "$STATE"/vm-*; do
           [[ -e "$m" ]] || continue
           p="${m##*/vm-}" c=colima s=Stopped cpus=5 mem=8 dsk=100
           [[ -z "${COLIMA_PROFILE:-}" || "$p" == "$COLIMA_PROFILE" ]] || continue
           [[ "$p" == default ]] || c="colima-$p"
           [[ ! -e "$STATE/running-$c" ]] || s=Running
           if [[ -f "$STATE/size-$p" ]]; then read -r cpus mem dsk < "$STATE/size-$p"; fi
           printf '{"name":"%s","status":"%s","arch":"aarch64","cpus":%s,"memory":%s,"disk":%s,"runtime":"docker"}\n' \
             "$p" "$s" "$cpus" $(( mem * 1073741824 )) $(( dsk * 1073741824 ))
         done ;;
  start) if [[ "${MAC_ARCH:-arm64}" != arm64 && " $* " == *" --vz-rosetta "* ]]; then
           echo "Error: unknown flag: --vz-rosetta" >&2; exit 1
         fi
         used=0; [[ ! -e "$datadisk" ]] || used=1
         if [[ ! -e "$STATE/vm-$profile" ]]; then
           if [[ -e "$STATE/fail-start-krunkit" && " $* " == *" --vm-type krunkit "* ]]; then
             echo "FATA[0003] error starting vm: krunkit exited" >&2; exit 1
           fi
           if [[ " $* " == *" --vm-type "* ]]; then
             all="$*"; type="${all##*--vm-type }"
             mkdir -p "$lima/$ctx"; echo "vmType: ${type%% *}" > "$lima/$ctx/lima.yaml"
           fi
           cpus=5 mem=8 dsk=100 prev=""
           : > "$STATE/mounts-$profile"
           for a in "$@"; do
             case "$prev" in
               --cpu)    cpus="$a" ;;
               --memory) mem="$a" ;;
               --disk)   dsk="$a" ;;
               --mount)  echo "$a" >> "$STATE/mounts-$profile" ;;
             esac
             prev="$a"
           done
           echo "$cpus $mem $dsk" > "$STATE/size-$profile"
           if [[ ! -e "$STATE/one-disk" ]]; then mkdir -p "${datadisk%/*}"; touch "$datadisk"; fi
         fi
         rm -f "$STATE/root-disk-$ctx"
         if krunkit && { [[ -e "$STATE/fail-mount" ]] \
              || { (( used )) && ! grep -q 'by-label/lima-' "$lima/_config/override.yaml" 2>/dev/null; }; }; then
           touch "$STATE/root-disk-$ctx"
         fi
         touch "$STATE/vm-$profile" "$STATE/running-$ctx"
         if [[ " $* " == *" --activate=false "* ]]; then touch "$STATE/quiet-$profile"; fi
         [[ -e "$STATE/quiet-$profile" ]] || echo "$ctx" > "$STATE/current-context" ;;
  stop)  for f in "$data/run-$ctx"/*; do
           [[ -e "$f" ]] || continue
           # Docker removes a --rm container, and the anonymous volumes it holds.
           if [[ -e "$STATE/rm-$ctx/${f##*/}" ]]; then
             for v in $(cat "$f"); do [[ ! "$v" =~ ^[0-9a-f]{64}$ ]] || rm -rf "${data:?}/vol-$ctx/$v"; done
             rm "$f"
             continue
           fi
           case "$(cat "$STATE/policy-$ctx/${f##*/}" 2>/dev/null || echo always)" in
             always|unless-stopped) ;;
             *) mkdir -p "$data/stopped-$ctx"; mv "$f" "$data/stopped-$ctx/" ;;
           esac
         done
         rm -f "$STATE/running-$ctx"; docker context rm --force "$ctx" > /dev/null ;;
  delete) [[ " $* " == *" --force "* || " $* " == *" -f "* ]] || { echo "Are you sure? [y/N]" >&2; exit 1; }
         rm -f "$STATE/vm-$profile" "$STATE/running-$ctx" "$STATE/size-$profile" "$STATE/quiet-$profile" \
           "$STATE/root-disk-$ctx"
         rm -rf "${lima:?}/${ctx:?}" "${home:?}/${profile:?}" "${STATE:?}/rootfs"
         # Without a data disk, Docker's data is on the root disk, which goes with the VM.
         if [[ " $* " == *" --data "* || " $* " == *" -d "* || ! -e "$datadisk" ]]; then
           rm -rf "${STATE:?}/vol-${ctx:?}" "${STATE:?}/run-${ctx:?}" "${STATE:?}/stopped-${ctx:?}" \
             "${STATE:?}/images-${ctx:?}" "${lima:?}/_disks/${ctx:?}"
         fi
         docker context rm --force "$ctx" > /dev/null ;;
  ssh)   cat > /dev/null
         [[ ! -e "$STATE/fail-ssh" ]] || exit 255
         if [[ -e "$STATE/fail-ssh-krunkit" ]] && krunkit; then exit 255; fi
         while [[ $# -gt 0 && "$1" != -- ]]; do shift; done
         shift; DOCKER_CONTEXT="$ctx" "$@" ;;
esac""",
    # Inside a Colima VM, named by the context the ssh stand-in exports:
    # Docker's and containerd's folders come from the data disk, from the
    # root disk after abiosoft/colima#1614 struck ($STATE/root-containerd for
    # containerd's alone), or are no mount at all on a VM made before Colima
    # had a data disk ($STATE/one-disk). -T names the disk that holds any
    # other folder: Lima's mount for one of the Mac's, tmpfs for /run, the
    # data disk under Docker's folder, else the root disk. $STATE/no-findmnt:
    # a VM without findmnt.
    "findmnt": r"""[[ ! -e "$STATE/no-findmnt" ]] || exit 127
target="${@: -1}"
case "$target" in
  /var/lib/docker|/var/lib/containerd)
    dir="${target##*/}"
    if [[ -e "$STATE/one-disk" ]]; then
      [[ " $* " == *" -T "* ]] || exit 1
      echo /dev/vda1
    elif [[ -e "$STATE/root-disk-$DOCKER_CONTEXT" || -e "$STATE/root-$dir" ]]; then echo "/dev/vda1[/mnt/lima-colima/$dir]"
    else echo "/dev/vdc1[/$dir]"; fi ;;
  /) echo /dev/vda1 ;;
  *) [[ " $* " == *" -T "* ]] || exit 1
     case "$target" in
       "$HOME"/*|/Volumes/*) echo lima-2b940935a64ea40c ;;
       /run/*|/var/run/*)    echo tmpfs ;;
       /var/lib/docker/*)    echo "/dev/vdc1[/docker]" ;;
       *)                    echo /dev/vda1 ;;
     esac ;;
esac""",
    # The Mac's free space: STAGE_FREE_KIB, or DISK_FREE_KIB on DISK_DEV for
    # Colima's disks folder or Docker Desktop's data folder. One disk unless
    # DISK_DEV says otherwise. A path that does not exist fails, as it does
    # with df itself.
    "df": r"""path="${@: -1}"
[[ -e "$path" ]] || { echo "df: $path: No such file or directory" >&2; exit 1; }
dev=/dev/disk1 free="${STAGE_FREE_KIB:-104857600}"
if [[ "$path" == *_disks* || "$path" == *DockerDesktop* ]]; then
  dev="${DISK_DEV:-/dev/disk1}" free="${DISK_FREE_KIB:-104857600}"
fi
printf 'Filesystem 1024-blocks Used Available Capacity Mounted on\n%s 209715200 0 %s 1%% /\n' "$dev" "$free" """,
    "open": r"""echo "open $*" >> "$STATE/calls"
case "${@: -1}" in
  Docker)   touch "$STATE/running-desktop-linux"; echo desktop-linux > "$STATE/current-context" ;;
  OrbStack) touch "$STATE/running-orbstack" ;;
esac""",
    # Login Items come from $STATE/login-items, as System Events lists them;
    # $STATE/hang-login-items holds the question, as a pending consent prompt does.
    # $STATE/headless-orbstack: OrbStack's engine runs without its app, as
    # after orb start, so the app is not running and quitting it stops nothing.
    "osascript": r"""echo "osascript $*" >> "$STATE/calls"
case "$*" in
  *'every login item'*) [[ ! -e "$STATE/hang-login-items" ]] || exec sleep 60
                        cat "$STATE/login-items" 2>/dev/null || true ;;
  *'application "Docker" is running'*)   if [[ -e "$STATE/running-desktop-linux" ]]; then echo true; else echo false; fi ;;
  *'application "OrbStack" is running'*)
    if [[ -e "$STATE/running-orbstack" && ! -e "$STATE/headless-orbstack" ]]; then echo true; else echo false; fi ;;
  *'"Docker"'*)   rm -f "$STATE/running-desktop-linux" ;;
  *'"OrbStack"'*) [[ -e "$STATE/headless-orbstack" ]] || rm -f "$STATE/running-orbstack" ;;
esac""",
    # OrbStack's own CLI, which stops its engine with or without the app.
    "orb": r"""echo "orb $*" >> "$STATE/calls"
if [[ "${1:-}" == stop ]]; then rm -f "$STATE/running-orbstack" "$STATE/headless-orbstack"; fi""",
    "sysctl": r"""case "$2" in
  hw.memsize) echo $(( ${MAC_RAM_GIB:-32} * 1073741824 )) ;;
  hw.ncpu)    echo "${MAC_CORES:-10}" ;;
esac""",
    "uname": r"""if [[ "${1:-}" == -m ]]; then echo "${MAC_ARCH:-arm64}"; else exec /usr/bin/uname "$@"; fi""",
    "docker-credential-osxkeychain": "exit 0",
}


@unittest.skipUnless(sys.platform == "darwin", "sage-runtime is for macOS")
class Mac(unittest.TestCase):
    """A fake Mac: its own home, apps folder, Homebrew prefix and stand-in commands."""

    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        root = Path(scratch.name)
        self.home, self.apps, self.state, self.bin = root / "home", root / "Applications", root / "state", root / "bin"
        self.brew = root / "brew"
        for folder in (self.home, self.apps, self.state, self.bin):
            folder.mkdir()
        for name, body in STUBS.items():
            self.stub(name, body)
        # This Mac's own /var/run/docker.sock and /usr/local/bin stay out of it.
        self.socket, self.sysbin = root / "var" / "run" / "docker.sock", root / "usr" / "local" / "bin"
        self.env = {"HOME": str(self.home), "PATH": f"{self.bin}:/usr/bin:/bin",
                    "STATE": str(self.state), "APPLICATIONS_DIR": str(self.apps),
                    "HOMEBREW_PREFIX": str(self.brew), "SYSTEM_SOCKET": str(self.socket),
                    "SYSTEM_BIN": str(self.sysbin)}
        self.config = self.home / ".docker" / "config.json"
        self.override = self.home / ".colima" / "_lima" / "_config" / "override.yaml"

    def stub(self, name, body):
        stub = self.bin / name
        stub.write_text("#!/bin/bash\n" + body + "\n")
        stub.chmod(0o755)

    def install(self, app):
        (self.apps / f"{app}.app").mkdir()

    def running(self, context):
        (self.state / f"running-{context}").touch()
        if context.startswith("colima"):  # a Colima VM that runs is one Colima lists
            (self.state / ("vm-" + (context.partition("-")[2] or "default"))).touch()

    def is_running(self, context):
        return (self.state / f"running-{context}").exists()

    def volume(self, context, name, files):
        folder = self.state / f"vol-{context}" / name
        folder.mkdir(parents=True)
        for rel, text in files.items():
            (folder / rel).parent.mkdir(parents=True, exist_ok=True)
            (folder / rel).write_text(text)
        return folder

    def container(self, context, name, *mounts, stopped=False, policy=None, rm=False):
        folder = self.state / f"{'stopped' if stopped else 'run'}-{context}"
        folder.mkdir(exist_ok=True)
        (folder / name).write_text(" ".join(str(m) for m in mounts) + "\n")
        if rm:  # docker run --rm, which takes no restart policy
            policy = "no"
            (self.state / f"rm-{context}").mkdir(exist_ok=True)
            (self.state / f"rm-{context}" / name).touch()
        if policy:
            (self.state / f"policy-{context}").mkdir(exist_ok=True)
            (self.state / f"policy-{context}" / name).write_text(policy + "\n")

    def docker_config(self, data):
        self.config.parent.mkdir(exist_ok=True)
        self.config.write_text(json.dumps(data))

    def run_tool(self, *args, **env):
        return subprocess.run([TOOL, *args], env=self.env | env, capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=30)

    def ok(self, *args, **env):
        result = self.run_tool(*args, **env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def colima(self, *args):
        """Colima, run by hand."""
        subprocess.run([self.bin / "colima", *args], env=self.env, check=True, capture_output=True)

    def start_tool(self, *args):
        return subprocess.Popen([TOOL, *args], env=self.env, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def wait_for(self, path):
        deadline = time.monotonic() + 20
        while not path.exists():
            self.assertLess(time.monotonic(), deadline, f"{path.name} never appeared")
            time.sleep(0.05)

    def calls(self):
        path = self.state / "calls"
        return path.read_text().splitlines() if path.exists() else []

    def started(self):
        """Every call that starts a runtime or makes a VM."""
        return [c for c in self.calls() if c.startswith(("colima start", "open "))]

    def context(self):
        """docker's saved context, the one new shells use."""
        return (self.state / "current-context").read_text().strip()

    def saved(self):
        path = self.home / ".sage-is" / "runtime"
        return path.read_text().strip() if path.exists() else None


class Use(Mac):
    def setUp(self):
        super().setUp()
        self.install("Docker")
        self.running("desktop-linux")

    def test_without_krunkit_apple_silicon_warns_and_falls_back_to_vz_with_rosetta(self):
        out = self.ok("use", "colima").stdout
        self.assertIn("colima start --profile default --vm-type vz --vz-rosetta --mount-type virtiofs "
                      "--mount-inotify --memory 8 --cpu 5 --disk 100", self.calls())
        self.assertIn("Warning: krunkit, the default VM on Apple Silicon, is not installed, so this VM uses vz. "
                      f"Get it: {KRUNKIT_INSTALL}", out)
        self.assertFalse(self.override.exists())

    def test_with_krunkit_the_vm_gives_memory_back_so_it_may_take_more(self):
        self.stub("krunkit", "exit 0")
        out = self.ok("use", "colima").stdout
        self.assertIn("colima start --profile default --vm-type krunkit --mount-type virtiofs "
                      "--mount-inotify --memory 12 --cpu 5 --disk 100", self.calls())
        self.assertEqual(self.override.read_text(), VIRTIOFS + DATA_DISK)
        self.assertIn("A QEMU Colima profile will not start while that line is there.", out)
        self.assertIn("mounts the VM's data disk (abiosoft/colima#1614)", out)
        self.assertNotIn("Warning", out)

    def test_the_override_keeps_what_it_held_and_each_fix_lands_once(self):
        self.stub("krunkit", "exit 0")
        self.override.parent.mkdir(parents=True)
        for held in ("cpuType: host\n", "cpuType: host"):  # an editor may leave off the last newline
            with self.subTest(held=held):
                self.override.write_text(held)
                for _ in range(2):
                    for marker in ("vm-default", "running-colima"):  # the VM deleted, so use creates one
                        (self.state / marker).unlink(missing_ok=True)
                    self.ok("use", "colima", MAC_RAM_GIB="16")
                self.assertEqual(self.override.read_text(), "cpuType: host\n" + VIRTIOFS + DATA_DISK)
        self.assertIn("--memory 8", " ".join(self.calls()))

    def test_an_override_with_its_own_provision_list_gets_the_step_to_add_by_hand(self):
        # A second provision key would break the file.
        self.stub("krunkit", "exit 0")
        self.override.parent.mkdir(parents=True)
        held = "provision:\n  - mode: system\n    script: echo hi\n"
        self.override.write_text(held)
        result = self.ok("use", "colima")
        self.assertEqual(self.override.read_text(), held + VIRTIOFS)
        self.assertIn(f"{self.override} has its own provision list. Add this step to it", result.stderr)
        self.assertIn(DATA_DISK_STEP, result.stderr)

    def test_a_krunkit_vm_keeps_docker_on_its_data_disk_after_a_restart(self):
        self.stub("krunkit", "exit 0")
        self.ok("use", "colima")
        self.colima("stop")
        self.ok("use", "colima")
        self.assertIn("colima start", self.calls())
        self.assertFalse((self.state / "root-disk-colima").exists())

    def test_docker_on_the_root_disk_stops_the_command_and_a_restart_mends_it(self):
        # A krunkit VM made without the boot step, by hand or by an older tool, after its first boot.
        self.stub("krunkit", "exit 0")
        self.override.parent.mkdir(parents=True)
        self.override.write_text(VIRTIOFS)
        self.colima("start", "--vm-type", "krunkit")
        self.colima("stop")
        result = self.run_tool("use", "colima")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Docker in the colima VM runs on the VM's root disk, not its data disk", result.stderr)
        self.assertIn("then run this again: colima restart", result.stderr)
        self.assertIsNone(self.saved())
        self.assertEqual(self.override.read_text(), VIRTIOFS + DATA_DISK)
        for step in ("stop", "start"):  # colima restart
            self.colima(step)
        self.ok("use", "colima")
        self.assertEqual(self.saved(), "colima")

    def test_a_vm_without_a_data_disk_passes_and_a_question_with_no_answer_only_warns(self):
        # Colima before its data disk kept Docker on the root disk by design.
        (self.state / "one-disk").touch()
        self.assertNotIn("Warning", self.ok("use", "colima").stderr)
        for fault in ("fail-ssh", "no-findmnt"):
            with self.subTest(fault):
                (self.state / fault).touch()
                result = self.ok("use", "colima")
                self.assertIn("Warning: could not ask the colima VM which disk Docker uses", result.stderr)
                self.assertEqual(self.saved(), "colima")
                (self.state / fault).unlink()

    def test_colimas_and_limas_homes_are_where_colima_finds_them(self):
        # Colima uses $XDG_CONFIG_HOME/colima while ~/.colima is missing, and Lima obeys LIMA_HOME.
        self.stub("krunkit", "exit 0")
        xdg = self.home / ".config"
        for name, env, lima in (("xdg-data", {"XDG_CONFIG_HOME": str(xdg)}, xdg / "colima" / "_lima"),
                                ("lima-data", {"LIMA_HOME": str(self.home / "lima")}, self.home / "lima")):
            with self.subTest(env):
                for marker in ("vm-default", "running-colima"):
                    (self.state / marker).unlink(missing_ok=True)
                self.ok("use", "colima", **env)
                self.assertEqual((lima / "_config" / "override.yaml").read_text(), VIRTIOFS + DATA_DISK)
                self.assertFalse((self.home / ".colima").exists())
                self.volume("desktop-linux", name, {"f": "1"})
                self.ok("copy-volume", name, **env)  # it measures the disk under Lima's home

    def test_an_exported_colima_profile_does_not_turn_use_to_another_vm(self):
        # A build shell may export COLIMA_PROFILE=build beside DOCKER_CONTEXT=colima-build.
        self.running("colima")
        self.running("colima-build")
        settings = self.home / "Library/Group Containers/group.com.docker/settings-store.json"
        settings.parent.mkdir(parents=True)
        settings.touch()
        self.ok("use", "docker-desktop", COLIMA_PROFILE="build")
        self.assertFalse(self.is_running("colima"))
        self.assertTrue(self.is_running("colima-build"))
        self.ok("use", "colima", COLIMA_PROFILE="build")
        self.assertIn("colima start", self.calls())
        self.assertNotIn("--vm-type", " ".join(self.calls()))
        self.assertTrue(self.is_running("colima"))

    def test_an_intel_mac_uses_vz_without_rosetta_and_without_a_warning(self):
        self.stub("krunkit", "exit 0")
        out = self.ok("use", "colima", MAC_ARCH="x86_64").stdout
        self.assertIn("colima start --profile default --vm-type vz --mount-type virtiofs "
                      "--mount-inotify --memory 8 --cpu 5 --disk 100", self.calls())
        self.assertNotIn("Warning", out)
        self.assertFalse(self.override.exists())

    def test_a_small_mac_gets_the_floor_and_overrides_win(self):
        self.ok("use", "colima", MAC_RAM_GIB="16", MAC_CORES="4")
        self.assertIn("--memory 5 --cpu 2", " ".join(self.calls()))
        for marker in ("vm-default", "running-colima"):  # the VM deleted, so the next use creates one
            (self.state / marker).unlink()
        self.ok("use", "colima", SAGE_RUNTIME_MEMORY="6", SAGE_RUNTIME_DISK="60")
        starts = [call for call in self.calls() if call.startswith("colima start")]
        self.assertIn("--memory 6 --cpu 5 --disk 60", starts[-1])

    def test_an_existing_vm_keeps_its_own_settings(self):
        (self.state / "vm-default").touch()
        self.ok("use", "colima")
        self.assertIn("colima start", self.calls())
        self.assertNotIn("--vm-type", " ".join(self.calls()))

    def test_use_points_docker_at_it_remembers_it_and_stops_the_other(self):
        result = self.ok("use", "colima")
        self.assertEqual(self.context(), "colima")
        self.assertEqual(self.saved(), "colima")
        self.assertFalse(self.is_running("desktop-linux"))
        self.assertIn("ai-ui and trellis-crm follow", result.stdout)

    def test_an_app_whose_engine_never_answers_is_still_stopped_first(self):
        # Its containers may still run, so a probe that times out is not "stopped".
        (self.state / "hang-desktop-linux").touch()
        self.ok("use", "colima")
        self.assertIn('osascript -e quit app "Docker"', self.calls())
        self.assertFalse(self.is_running("desktop-linux"))

    def test_keep_other_leaves_the_other_running(self):
        self.ok("use", "colima", "--keep-other")
        self.assertTrue(self.is_running("desktop-linux"))
        self.assertTrue(self.is_running("colima"))

    def test_back_to_docker_desktop_stops_colima(self):
        self.ok("use", "colima")
        settings = self.home / "Library/Group Containers/group.com.docker/settings-store.json"
        settings.parent.mkdir(parents=True)
        settings.touch()
        self.ok("use", "docker-desktop")
        self.assertIn("open -g -j -a Docker", self.calls())
        self.assertIn("colima stop", self.calls())
        self.assertEqual(self.saved(), "docker-desktop")
        self.assertEqual(self.context(), "desktop-linux")

    def test_an_unknown_or_missing_runtime_is_refused(self):
        result = self.run_tool("use", "podman")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Use one of: colima docker-desktop orbstack", result.stderr)
        result = self.run_tool("use", "orbstack")
        self.assertEqual(result.returncode, 1)
        self.assertIn("brew install --cask orbstack", result.stderr)
        self.assertIsNone(self.saved())


class SharedData(Mac):
    """Two VMs never share file locks, so ~/SageData has one runtime at a time.

    Docker Desktop lists a bind source under /host_mnt, as its VM sees it; the
    docker stand-in does the same."""

    def setUp(self):
        super().setUp()
        self.install("Docker")
        self.running("desktop-linux")
        (self.state / "vm-default").touch()
        self.shared = self.home / "SageData" / "sprig-registry"

    def test_switching_stops_the_other_runtime_before_starting_this_one(self):
        self.ok("use", "colima")
        calls = self.calls()
        quit_desktop = calls.index('osascript -e quit app "Docker"')
        start_colima = next(i for i, c in enumerate(calls) if c.startswith("colima start"))
        self.assertLess(quit_desktop, start_colima)

    def test_keep_other_is_refused_while_both_sides_use_the_shared_folder(self):
        self.running("colima")
        self.container("colima", "local-registry", self.shared)
        self.container("desktop-linux", "old-registry", self.shared)
        result = self.run_tool("use", "colima", "--keep-other")
        self.assertEqual(result.returncode, 1)
        self.assertIn("local-registry", result.stderr)
        self.assertIn("old-registry", result.stderr)
        self.assertIn("lose data", result.stderr)

    def test_a_folder_that_only_shares_the_name_does_not_count(self):
        self.running("colima")
        self.container("colima", "local-registry", self.shared)
        self.container("desktop-linux", "old", self.home / "SageDataOld" / "x", "trellis-data")
        self.ok("use", "colima", "--keep-other")

    def test_keep_other_is_fine_when_only_one_side_uses_it(self):
        self.running("colima")
        self.container("colima", "local-registry", self.shared)
        self.container("desktop-linux", "web", "/var/lib/docker/volumes/x/_data")
        self.ok("use", "colima", "--keep-other")

    def test_a_copy_is_refused_while_both_sides_use_the_shared_folder(self):
        self.running("colima")
        (self.home / ".sage-is").mkdir()
        (self.home / ".sage-is" / "runtime").write_text("colima\n")
        self.container("colima", "a", self.shared)
        self.container("desktop-linux", "b", self.shared / "x")
        self.volume("desktop-linux", "trellis-data", {"f": "1"})
        result = self.run_tool("copy-volume", "trellis-data")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Stop one side first", result.stderr)

    def test_a_refusal_stops_the_runtime_the_command_started(self):
        # Colima's registry has a restart policy, so it comes back as Colima starts.
        self.container("colima", "local-registry", self.shared)
        self.container("desktop-linux", "old-registry", self.shared)
        (self.home / ".sage-is").mkdir()
        (self.home / ".sage-is" / "runtime").write_text("colima\n")
        self.volume("desktop-linux", "trellis-data", {"f": "1"})
        for args in (("migrate",), ("copy-volume", "trellis-data")):
            with self.subTest(args):
                result = self.run_tool(*args)
                self.assertEqual(result.returncode, 1)
                self.assertIn("Stop one side first", result.stderr)
                self.assertIn("Stopping colima...", result.stdout)
                self.assertFalse(self.is_running("colima"))
                self.assertTrue(self.is_running("desktop-linux"))

    def test_a_listing_that_fails_is_not_taken_for_no_containers(self):
        self.running("colima")
        self.container("colima", "local-registry", self.shared)
        (self.state / "fail-ps-desktop-linux").touch()
        result = self.run_tool("use", "colima", "--keep-other")
        self.assertEqual(result.returncode, 1)
        self.assertIn("could not list the containers in docker-desktop", result.stderr)
        self.assertIsNone(self.saved())

    def test_an_app_that_is_up_is_asked_even_when_its_engine_does_not_answer_info(self):
        self.running("colima")
        self.container("colima", "local-registry", self.shared)
        self.container("desktop-linux", "old-registry", self.shared)
        (self.state / "fail-info-desktop-linux").touch()
        result = self.run_tool("use", "colima", "--keep-other")
        self.assertEqual(result.returncode, 1)
        self.assertIn("old-registry", result.stderr)


class DockerConfig(Mac):
    def test_a_credential_helper_that_left_with_docker_desktop_becomes_the_keychain(self):
        self.docker_config({"auths": {"ghcr.io": {}}, "credsStore": "desktop"})
        self.ok("use", "colima")
        data = json.loads(self.config.read_text())
        self.assertEqual(data["credsStore"], "osxkeychain")
        self.assertEqual(data["auths"], {"ghcr.io": {}})
        backups = list(self.config.parent.glob("config.json.*.bak"))
        self.assertEqual(len(backups), 1)
        self.assertIn('"desktop"', backups[0].read_text())

    def test_a_helper_that_exists_is_left_alone(self):
        self.docker_config({"credsStore": "osxkeychain"})
        self.ok("use", "colima")
        self.assertEqual(json.loads(self.config.read_text())["credsStore"], "osxkeychain")
        self.assertFalse(list(self.config.parent.glob("*.bak")))

    def test_homebrews_plugin_folder_is_added_once(self):
        (self.brew / "lib/docker/cli-plugins").mkdir(parents=True)
        self.docker_config({"cliPluginsExtraDirs": ["/elsewhere"]})
        self.ok("use", "colima")
        self.ok("use", "colima")
        dirs = json.loads(self.config.read_text())["cliPluginsExtraDirs"]
        self.assertEqual(dirs, [str(self.brew / "lib/docker/cli-plugins"), "/elsewhere"])

    def test_no_config_and_no_homebrew_plugins_writes_nothing(self):
        self.ok("use", "colima")
        self.assertFalse(self.config.exists())

    def test_without_homebrew_prefix_brew_names_its_plugin_folder(self):
        # On Intel the prefix is /usr/local, and an /opt/homebrew guess misses it.
        prefix = self.brew.parent / "usr-local"
        (prefix / "lib/docker/cli-plugins").mkdir(parents=True)
        self.ok("use", "colima", HOMEBREW_PREFIX="")  # no brew on PATH
        self.assertFalse(self.config.exists())
        self.stub("brew", f'[[ "$*" == --prefix ]] && echo "{prefix}"')
        self.ok("use", "colima", HOMEBREW_PREFIX="")
        config = {"cliPluginsExtraDirs": [str(prefix / "lib/docker/cli-plugins")]}
        self.assertEqual(json.loads(self.config.read_text()), config)
        self.install("Docker")
        self.ok("use", "docker-desktop", HOMEBREW_PREFIX="")
        self.assertEqual(json.loads(self.config.read_text()), config)
        self.assertEqual(self.context(), "desktop-linux")

    def desktop_helper(self):
        """Docker Desktop installed with its credential helper, which wraps the Keychain one."""
        self.install("Docker")
        self.stub("docker-credential-desktop", "exit 0")

    def test_with_colima_in_use_the_keychain_takes_over_from_docker_desktops_helper(self):
        self.desktop_helper()
        self.install("OrbStack")
        self.docker_config({"auths": {"ghcr.io": {}}, "credsStore": "desktop"})
        out = self.ok("use", "colima").stdout
        self.assertIn(f"credsStore: desktop goes through Docker Desktop; now osxkeychain (backup: {self.config}.", out)
        self.assertEqual(json.loads(self.config.read_text()), {"auths": {"ghcr.io": {}}, "credsStore": "osxkeychain"})
        backups = list(self.config.parent.glob("config.json.*.bak"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(json.loads(backups[0].read_text())["credsStore"], "desktop")
        # Back on Docker Desktop, or on OrbStack, the helper Docker Desktop put back as it started stays.
        for runtime in ("docker-desktop", "orbstack"):
            with self.subTest(runtime):
                self.docker_config({"auths": {"ghcr.io": {}}, "credsStore": "desktop"})
                self.ok("use", runtime)
                self.assertEqual(self.saved(), runtime)
                self.assertEqual(json.loads(self.config.read_text())["credsStore"], "desktop")
        self.assertEqual(len(list(self.config.parent.glob("*.bak"))), 1)

    def test_without_the_keychain_helper_docker_desktops_stays(self):
        self.desktop_helper()
        (self.bin / "docker-credential-osxkeychain").unlink()
        self.docker_config({"credsStore": "desktop"})
        self.ok("use", "colima")
        self.assertEqual(json.loads(self.config.read_text()), {"credsStore": "desktop"})
        self.assertFalse(list(self.config.parent.glob("*.bak")))

    def test_plugin_links_into_a_docker_app_that_is_gone_go_and_the_rest_stay(self):
        plugins = self.home / ".docker" / "cli-plugins"
        plugins.mkdir(parents=True)
        bundled = self.apps / "Docker.app" / "Contents" / "Resources" / "cli-plugins"
        bundled.mkdir(parents=True)
        for name in ("docker-ai", "docker-compose"):
            (bundled / name).touch()
            (plugins / name).symlink_to(bundled / name)
        (plugins / "docker-offline").symlink_to("/Volumes/Unplugged/docker-offline")  # a drive not mounted now
        (plugins / "docker-own").touch()
        everything = ["docker-ai", "docker-compose", "docker-offline", "docker-own"]
        self.assertNotIn("cli-plugins", self.ok("status").stdout)
        self.ok("use", "colima")
        self.assertEqual(sorted(p.name for p in plugins.iterdir()), everything)
        shutil.rmtree(self.apps / "Docker.app")  # dragged to the Trash
        self.assertIn(f"{plugins} holds links into Docker.app that lead nowhere: docker-ai docker-compose. docker "
                      "lists each as a broken plugin. Remove them: sage-runtime use colima\n", self.ok("status").stdout)
        self.assertEqual(sorted(p.name for p in plugins.iterdir()), everything)  # status only reads
        out = self.ok("use", "colima").stdout
        self.assertIn("cli-plugins: removed docker-ai docker-compose, which led nowhere in Docker.app\n", out)
        self.assertEqual(sorted(p.name for p in plugins.iterdir()), ["docker-offline", "docker-own"])
        self.assertNotIn("cli-plugins", self.ok("status").stdout)
        self.install("Docker")  # installed again
        self.ok("use", "docker-desktop")
        self.assertEqual(self.context(), "desktop-linux")
        self.assertEqual(sorted(p.name for p in plugins.iterdir()), ["docker-offline", "docker-own"])

    def test_a_plugin_an_update_dropped_from_docker_app_goes_and_the_rest_stay(self):
        # Docker Desktop stopped shipping docker-sbom, and left its link behind.
        plugins = self.home / ".docker" / "cli-plugins"
        plugins.mkdir(parents=True)
        bundled = self.apps / "Docker.app" / "Contents" / "Resources" / "cli-plugins"
        bundled.mkdir(parents=True)
        (bundled / "docker-scout").touch()
        for name in ("docker-sbom", "docker-scout"):
            (plugins / name).symlink_to(bundled / name)
        out = self.ok("status").stdout
        self.assertIn(f"{plugins} holds links into Docker.app that lead nowhere: docker-sbom.", out)
        out = self.ok("use", "colima").stdout
        self.assertIn("cli-plugins: removed docker-sbom, which led nowhere in Docker.app\n", out)
        self.assertEqual([p.name for p in plugins.iterdir()], ["docker-scout"])
        self.ok("use", "docker-desktop")
        self.assertEqual(self.context(), "desktop-linux")
        self.assertEqual([p.name for p in plugins.iterdir()], ["docker-scout"])


class Status(Mac):
    def test_it_lists_runtimes_and_the_problems_that_will_bite(self):
        self.install("Docker")
        self.running("desktop-linux")
        (self.state / "vm-default").touch()
        self.volume("desktop-linux", "trellis-data", {"a": "1"})
        (self.state / "current-context").write_text("desktop-linux\n")
        (self.home / ".sage-is").mkdir()
        (self.home / ".sage-is" / "runtime").write_text("colima\n")
        self.docker_config({"credsStore": "desktop"})
        builders = self.home / ".docker" / "buildx" / "instances"
        builders.mkdir(parents=True)
        (builders / "old-builder").write_text(json.dumps({"Name": "old-builder", "Nodes": [{"Endpoint": "gone-context"}]}))
        # Colima drops a stopped VM's context and makes it again at start.
        (builders / "fine").write_text(json.dumps({"Name": "fine", "Nodes": [{"Endpoint": "colima"}]}))
        (self.state / "no-buildx").touch()
        out = self.ok("status").stdout
        self.assertIn("docker-desktop  running, 1 volumes", out)
        self.assertIn("colima          stopped  <- in use", out)
        self.assertIn("orbstack        not installed", out)
        self.assertIn("Not the runtime in use. Run: sage-runtime use colima", out)
        self.assertIn('credsStore "desktop" has no helper', out)
        self.assertNotIn("keeps docker's logins through Docker Desktop", out)  # its helper is gone
        self.assertIn("brew install docker-buildx", out)
        self.assertIn("docker buildx rm old-builder", out)
        self.assertNotIn("buildx rm fine", out)

    def test_a_clean_mac_says_so(self):
        self.running("colima")
        (self.state / "current-context").write_text("colima\n")
        self.assertIn("No problems found.", self.ok("status").stdout)

    def desktop_leftovers(self):
        """A Mac moved to Colima, with Docker Desktop still installed and set up as it leaves things."""
        self.install("Docker")
        self.stub("docker-credential-desktop", "exit 0")
        self.running("colima")
        (self.state / "current-context").write_text("colima\n")
        (self.home / ".sage-is").mkdir()
        (self.home / ".sage-is" / "runtime").write_text("colima\n")
        self.docker_config({"credsStore": "desktop", "features": {"hooks": "true"},
                            "plugins": {"scout": {"hooks": "pull,buildx build"}}})
        self.socket.parent.mkdir(parents=True)
        self.socket.symlink_to(self.home / ".docker" / "run" / "docker.sock")
        self.sysbin.mkdir(parents=True)
        tools = self.apps / "Docker.app" / "Contents" / "Resources" / "bin"
        for name in ("docker", "docker-credential-desktop", "kubectl"):
            (self.sysbin / name).symlink_to(tools / name)
        (self.sysbin / "docker-buildx").symlink_to("/opt/elsewhere/docker-buildx")
        (self.state / "login-items").write_text("Finder, Docker Desktop, Raycast\n")

    def test_it_names_what_docker_desktop_left_behind_with_each_mend_and_changes_none(self):
        self.desktop_leftovers()
        config = self.config.read_text()
        out = self.ok("status").stdout
        for line in (
                'credsStore "desktop" keeps docker\'s logins through Docker Desktop; the Keychain helper holds the same '
                "ones. Hand them over: sage-runtime use colima",
                f"{self.socket} leads to Docker Desktop, so tools that skip docker's context miss colima. Point it at "
                f"colima: sudo ln -sf {self.home}/.colima/default/docker.sock {self.socket}",
                f"{self.config} turns on Docker Desktop's plugin hooks, so docker calls its plugins after run, build and "
                f"pull. Turn them off: plutil -remove features.hooks {self.config}",
                f"{self.sysbin} holds links into Docker.app that lead nowhere or keep Homebrew's docker from linking "
                f"there. Remove them: sudo rm {self.sysbin}/docker {self.sysbin}/docker-credential-desktop",
                "A Login Item opens Docker Desktop at login, and it takes docker's context from colima. Remove it: "
                "osascript -e 'tell application \"System Events\" to delete login item \"Docker Desktop\"'"):
            self.assertIn(line + "\n", out)
        self.assertNotIn("No problems found.", out)
        self.assertEqual(self.config.read_text(), config)
        self.assertEqual(self.socket.readlink(), self.home / ".docker" / "run" / "docker.sock")
        self.assertEqual(sorted(p.name for p in self.sysbin.iterdir()),
                         ["docker", "docker-buildx", "docker-credential-desktop", "kubectl"])
        self.assertEqual([c for c in self.calls() if "login item" in c],
                         ['osascript -e tell application "System Events" to get the name of every login item'])

    def test_back_on_docker_desktop_its_own_setup_is_no_problem(self):
        self.desktop_leftovers()
        config = self.config.read_text()
        self.ok("use", "docker-desktop")
        self.assertEqual(self.config.read_text(), config)
        self.assertIn("No problems found.", self.ok("status").stdout)
        self.assertNotIn("login item", " ".join(self.calls()))

    def test_on_orbstack_the_socket_points_there_and_only_colima_asks_for_login_items(self):
        self.desktop_leftovers()
        self.install("OrbStack")
        (self.home / ".sage-is" / "runtime").write_text("orbstack\n")
        out = self.ok("status").stdout
        self.assertIn(f"Point it at orbstack: sudo ln -sf {self.home}/.orbstack/run/docker.sock {self.socket}\n", out)
        self.assertNotIn("credsStore", out)
        self.assertNotIn("login item", " ".join(self.calls()))

    def test_links_into_docker_app_that_still_work_count_only_where_homebrew_links_docker(self):
        # Docker Desktop stays installed as a fallback. On Apple Silicon Homebrew links into /opt/homebrew, so its
        # links in /usr/local/bin block nothing while they work; on Intel Homebrew links into /usr/local itself.
        self.desktop_leftovers()
        tools = self.apps / "Docker.app" / "Contents" / "Resources" / "bin"
        tools.mkdir(parents=True)
        for name in ("docker", "docker-credential-desktop"):
            (tools / name).touch()
        line = f"{self.sysbin} holds links into Docker.app"
        self.assertNotIn(line, self.ok("status").stdout)
        intel = self.ok("status", HOMEBREW_PREFIX=str(self.sysbin.parent)).stdout
        self.assertIn(f"Remove them: sudo rm {self.sysbin}/docker {self.sysbin}/docker-credential-desktop\n", intel)
        # Homebrew's formulae, installed but kept from linking there, must link once the links go, or docker is
        # gone. Homebrew may count one as linked already, so it unlinks first.
        (tools / "docker-credential-osxkeychain").touch()
        (self.sysbin / "docker-credential-osxkeychain").symlink_to(tools / "docker-credential-osxkeychain")
        for formula in ("docker", "docker-credential-helper"):
            (self.sysbin.parent / "Cellar" / formula).mkdir(parents=True)
        intel = self.ok("status", HOMEBREW_PREFIX=str(self.sysbin.parent)).stdout
        self.assertIn(f"Remove them: sudo rm {self.sysbin}/docker {self.sysbin}/docker-credential-desktop "
                      f"{self.sysbin}/docker-credential-osxkeychain; then link Homebrew's own: brew unlink docker "
                      "docker-credential-helper && brew link docker docker-credential-helper\n", intel)
        (tools / "docker").unlink()  # one that leads nowhere counts anywhere
        self.assertIn(f"Remove them: sudo rm {self.sysbin}/docker\n", self.ok("status").stdout)
        self.ok("use", "docker-desktop")
        self.assertEqual(self.context(), "desktop-linux")
        self.assertEqual(sorted(p.name for p in self.sysbin.iterdir()),
                         ["docker", "docker-buildx", "docker-credential-desktop", "docker-credential-osxkeychain",
                          "kubectl"])

    def test_login_items_are_asked_only_with_docker_desktop_installed_and_never_hold_status_up(self):
        self.desktop_leftovers()
        (self.state / "hang-login-items").touch()  # macOS asks first whether this terminal may
        began = time.monotonic()
        out = self.ok("status").stdout
        self.assertLess(time.monotonic() - began, 25)
        self.assertNotIn("Login Item", out)
        (self.state / "hang-login-items").unlink()
        shutil.rmtree(self.apps / "Docker.app")  # dragged to the Trash: its Login Item opens nothing
        (self.state / "calls").unlink()
        self.assertNotIn("Login Item", self.ok("status").stdout)
        self.assertNotIn("login item", " ".join(self.calls()))

    def test_only_a_login_item_named_docker_desktop_counts(self):
        self.desktop_leftovers()
        for items in ("Finder, Raycast\n", "Docker Desktop Helper, Finder\n", ""):
            with self.subTest(items=items):
                (self.state / "login-items").write_text(items)
                self.assertNotIn("Login Item", self.ok("status").stdout)
        (self.state / "login-items").write_text("Docker Desktop\n")
        self.assertIn("A Login Item opens Docker Desktop at login", self.ok("status").stdout)

    def test_an_engine_that_never_answers_is_named_and_does_not_hold_status_up(self):
        self.install("Docker")
        self.running("desktop-linux")
        (self.state / "hang-desktop-linux").touch()
        began = time.monotonic()
        out = self.ok("status").stdout
        self.assertIn("docker-desktop  up, but its engine does not answer\n  Stop it: sage-runtime use colima", out)
        self.assertNotIn("No problems found.", out)
        self.assertLess(time.monotonic() - began, 25)


class Copy(Mac):
    def setUp(self):
        super().setUp()
        self.install("Docker")
        (self.state / "vm-default").touch()
        self.ok("use", "colima")

    def test_a_volume_comes_across_whole(self):
        self.volume("desktop-linux", "trellis-data", {"trellis.sqlite3": "db", "artifacts/ab/cd": "blob"})
        result = self.ok("copy-volume", "trellis-data")
        copied = self.state / "vol-colima" / "trellis-data"
        self.assertEqual((copied / "trellis.sqlite3").read_text(), "db")
        self.assertEqual((copied / "artifacts/ab/cd").read_text(), "blob")
        self.assertIn("from docker-desktop to colima", result.stdout)
        self.assertIn("Copied 5 files and folders", result.stdout)

    def staged(self):
        stage = self.home / ".sage-is" / "staging"
        return sorted(p.name for p in stage.iterdir()) if stage.exists() else []

    def test_the_copy_passes_through_a_file_that_goes_when_done(self):
        self.volume("desktop-linux", "trellis-data", {"trellis.sqlite3": "db"})
        self.ok("copy-volume", "trellis-data")
        self.assertIn("-v " + str(self.home / ".sage-is" / "staging") + ":/stage", " ".join(self.calls()))
        self.assertEqual(self.staged(), [])

    def test_starting_docker_desktop_for_a_copy_leaves_docker_where_it_was(self):
        # A shell may export DOCKER_CONTEXT or DOCKER_HOST; docker's saved context goes back, quietly.
        for i, env in enumerate(({}, {"DOCKER_CONTEXT": "colima"}, {"DOCKER_HOST": "unix:///tmp/elsewhere.sock"})):
            with self.subTest(env=env):
                (self.state / "running-desktop-linux").unlink(missing_ok=True)
                self.volume("desktop-linux", f"data-{i}", {"f": "1"})
                result = self.ok("copy-volume", f"data-{i}", **env)
                self.assertIn("open -a Docker", self.calls())
                self.assertEqual(self.context(), "colima")
                self.assertNotIn("Current context is now", result.stdout + result.stderr)

    def test_a_copy_into_colima_stopped_by_hand_leaves_docker_on_colima(self):
        # Colima drops its context as it stops, which leaves docker on default.
        self.colima("stop")
        self.assertEqual(self.context(), "default")
        self.volume("desktop-linux", "trellis-data", {"f": "1"})
        self.ok("copy-volume", "trellis-data")
        self.assertIn("open -a Docker", self.calls())
        self.assertEqual(self.context(), "colima")

    def test_only_this_user_may_open_the_staging_folder(self):
        # A staged file is a whole volume or image, and another account on the Mac can read ~.
        stage = self.home / ".sage-is" / "staging"
        self.volume("desktop-linux", "trellis-data", {"f": "1"})
        (self.state / "images-desktop-linux").write_text("yt:latest\t369MB\n")
        for args in (("copy-volume", "trellis-data"), ("copy-image", "yt:latest")):
            with self.subTest(args):
                stage.mkdir(parents=True, exist_ok=True)
                stage.chmod(0o755)
                self.ok(*args)
                self.assertEqual(stat.S_IMODE(stage.stat().st_mode), 0o700)

    def test_a_runtime_that_never_answers_is_given_up_on_in_time(self):
        # Docker Desktop opens, and its engine never answers.
        self.volume("desktop-linux", "trellis-data", {"trellis.sqlite3": "db"})
        (self.state / "hang-desktop-linux").touch()
        began = time.monotonic()
        result = self.run_tool("copy-volume", "trellis-data", SAGE_RUNTIME_WAIT="3")
        self.assertEqual(result.returncode, 1)
        self.assertIn("docker-desktop did not answer within 3 seconds", result.stderr)
        self.assertLess(time.monotonic() - began, 25)
        self.assertEqual(self.context(), "colima")

    def test_a_failed_write_leaves_no_half_copy_to_be_skipped_later(self):
        self.volume("desktop-linux", "trellis-data", {"trellis.sqlite3": "db"})
        (self.state / "fail-xf").touch()
        result = self.run_tool("copy-volume", "trellis-data")
        self.assertEqual(result.returncode, 1)
        self.assertIn("could not write volume trellis-data", result.stderr)
        self.assertFalse((self.state / "vol-colima" / "trellis-data").exists())
        self.assertEqual(self.staged(), [])
        (self.state / "fail-xf").unlink()
        self.ok("copy-volume", "trellis-data")
        self.assertEqual((self.state / "vol-colima" / "trellis-data" / "trellis.sqlite3").read_text(), "db")

    def test_an_empty_target_that_was_there_is_left_empty_not_deleted(self):
        self.volume("desktop-linux", "trellis-data", {"trellis.sqlite3": "db"})
        self.volume("colima", "trellis-data", {})
        (self.state / "fail-xf").touch()
        self.assertEqual(self.run_tool("copy-volume", "trellis-data").returncode, 1)
        target = self.state / "vol-colima" / "trellis-data"
        self.assertTrue(target.exists())
        self.assertEqual(list(target.iterdir()), [])

    def test_force_keeps_the_old_data_when_the_source_cannot_be_read(self):
        self.volume("desktop-linux", "trellis-data", {"new": "1"})
        self.volume("colima", "trellis-data", {"old": "1"})
        (self.state / "fail-cf").touch()
        result = self.run_tool("copy-volume", "trellis-data", "--force")
        self.assertEqual(result.returncode, 1)
        self.assertIn("could not read volume trellis-data", result.stderr)
        self.assertTrue((self.state / "vol-colima" / "trellis-data" / "old").exists())
        self.assertEqual(self.staged(), [])

    def test_a_target_with_data_needs_force(self):
        self.volume("desktop-linux", "trellis-data", {"new": "1"})
        self.volume("colima", "trellis-data", {"old": "1"})
        result = self.run_tool("copy-volume", "trellis-data")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Add --force", result.stderr)
        self.assertFalse((self.state / "vol-colima" / "trellis-data" / "new").exists())
        self.ok("copy-volume", "trellis-data", "--force")
        self.assertTrue((self.state / "vol-colima" / "trellis-data" / "new").exists())

    def test_back_the_other_way_with_from_and_to(self):
        self.volume("colima", "sage-ai-dev-data", {"webui.db": "x"})
        self.ok("copy-volume", "sage-ai-dev-data", "--from", "colima", "--to", "docker-desktop")
        self.assertTrue((self.state / "vol-desktop-linux" / "sage-ai-dev-data" / "webui.db").exists())

    def test_a_missing_volume_or_image_is_named(self):
        for args, message in ((("copy-volume", "nope"), "docker-desktop has no volume nope"),
                              (("copy-image", "nope:1"), "docker-desktop has no image nope:1")):
            with self.subTest(args):
                result = self.run_tool(*args)
                self.assertEqual(result.returncode, 1)
                self.assertIn(message, result.stderr)

    def test_a_volume_in_use_on_either_side_is_not_copied(self):
        self.volume("desktop-linux", "trellis-data", {"trellis.sqlite3": "db"})
        self.container("desktop-linux", "trellis-crm", "trellis-data")
        result = self.run_tool("copy-volume", "trellis-data")
        self.assertEqual(result.returncode, 1)
        self.assertIn("trellis-data: trellis-crm (docker-desktop)", result.stderr)
        self.assertIn("Stop them, then run this again", result.stderr)
        (self.state / "run-desktop-linux" / "trellis-crm").unlink()
        self.container("colima", "trellis-web", "trellis-data")
        result = self.run_tool("copy-volume", "trellis-data")
        self.assertEqual(result.returncode, 1)
        self.assertIn("trellis-data: trellis-web (colima)", result.stderr)
        self.assertFalse((self.state / "vol-colima" / "trellis-data").exists())
        self.assertEqual(self.staged(), [])

    def test_a_container_on_another_volume_does_not_hold_a_copy_back(self):
        self.volume("desktop-linux", "trellis-data", {"trellis.sqlite3": "db"})
        self.container("desktop-linux", "old", "trellis-data-old", self.home / "trellis-data")
        self.ok("copy-volume", "trellis-data")

    def test_an_orbstack_engine_running_without_its_app_is_asked_and_stopped(self):
        # orb start runs OrbStack's engine with the app closed: macOS says the app is not running.
        self.install("OrbStack")
        self.running("orbstack")
        (self.state / "headless-orbstack").touch()
        # OrbStack keeps orb in ~/.orbstack/bin, which a minimal PATH leaves out.
        orbstack_bin = self.home / ".orbstack" / "bin"
        orbstack_bin.mkdir(parents=True)
        (self.bin / "orb").rename(orbstack_bin / "orb")
        self.volume("orbstack", "pgdata", {"PG_VERSION": "16"})
        self.container("orbstack", "postgres", "pgdata")
        for args in (("copy-volume", "pgdata", "--from", "orbstack", "--to", "colima"), ("migrate", "--from", "orbstack")):
            with self.subTest(args):
                result = self.run_tool(*args)
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn("pgdata: postgres (orbstack)", result.stderr)
                self.assertFalse((self.state / "vol-colima" / "pgdata").exists())
        self.ok("use", "colima")
        self.assertIn("orb stop", self.calls())
        self.assertFalse(self.is_running("orbstack"))
        self.ok("use", "orbstack")
        self.assertTrue(self.is_running("orbstack"))
        self.assertFalse(self.is_running("colima"))
        self.assertEqual((self.saved(), self.context()), ("orbstack", "orbstack"))

    def test_a_probe_that_fails_stops_the_copy_before_anything_changes(self):
        self.volume("desktop-linux", "trellis-data", {"trellis.sqlite3": "OLD"})
        kept = self.volume("colima", "trellis-data", {"trellis.sqlite3": "NEWER", "only-in-colima": "x"})
        for failure, message in (("fail-probe-colima", "could not look inside volume trellis-data in colima"),
                                 ("fail-volume-ls-colima", "could not list the volumes in colima")):
            with self.subTest(failure):
                (self.state / failure).touch()
                result = self.run_tool("copy-volume", "trellis-data")
                self.assertEqual(result.returncode, 1)
                self.assertIn(message, result.stderr)
                self.assertEqual((kept / "trellis.sqlite3").read_text(), "NEWER")
                self.assertTrue((kept / "only-in-colima").exists())
                (self.state / failure).unlink()

    def migrated(self):
        path = self.home / ".sage-is" / "migrated"
        return path.read_text().splitlines() if path.exists() else []

    def test_a_finished_copy_is_recorded_and_a_failed_one_drops_the_record(self):
        self.volume("desktop-linux", "trellis-data", {"trellis.sqlite3": "db"})
        self.ok("copy-volume", "trellis-data")
        self.assertEqual(len(self.migrated()), 1)
        self.assertEqual(self.migrated()[0].split()[:4], ["trellis-data", "docker-desktop", "colima", "2"])
        (self.state / "fail-xf").touch()
        self.assertEqual(self.run_tool("copy-volume", "trellis-data", "--force").returncode, 1)
        self.assertEqual(self.migrated(), [])

    def test_force_with_a_failed_write_leaves_the_target_empty_not_half_made(self):
        self.volume("desktop-linux", "trellis-data", {"a": "1", "b": "2"})
        target = self.volume("colima", "trellis-data", {"old": "1"})
        (self.state / "fail-xf").touch()
        self.assertEqual(self.run_tool("copy-volume", "trellis-data", "--force").returncode, 1)
        self.assertTrue(target.exists())
        self.assertEqual(list(target.iterdir()), [])
        self.assertEqual(self.staged(), [])

    def test_a_clear_that_fails_partway_leaves_the_target_empty_not_half_cleared(self):
        # --force gave up what the target held, and half of it would pass for a volume.
        self.volume("desktop-linux", "trellis-data", {"new": "1"})
        target = self.volume("colima", "trellis-data", {"a": "1", "b": "2"})
        (self.state / "fail-clear").touch()
        result = self.run_tool("copy-volume", "trellis-data", "--force")
        self.assertEqual(result.returncode, 1)
        self.assertIn("could not clear volume trellis-data in colima; run the copy again", result.stderr)
        self.assertTrue(target.exists())
        self.assertEqual(list(target.iterdir()), [])
        self.assertEqual(self.staged(), [])
        self.assertEqual(self.migrated(), [])

    def test_an_interrupted_copy_takes_its_staged_file_and_half_made_volume_with_it(self):
        self.volume("desktop-linux", "trellis-data", {"trellis.sqlite3": "db"})
        (self.state / "hold-xf").touch()
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            with self.subTest(sig.name):
                tool = self.start_tool("copy-volume", "trellis-data")
                self.wait_for(self.state / "extracting")
                self.assertEqual(self.staged(), ["trellis-data.tar"])
                tool.send_signal(sig)
                (self.state / "release").touch()
                tool.communicate(timeout=30)
                self.assertEqual(tool.returncode, 128 + sig)
                self.assertEqual(self.staged(), [])
                self.assertFalse((self.state / "vol-colima" / "trellis-data").exists())
                self.assertEqual(self.migrated(), [])
                for marker in ("extracting", "release"):
                    (self.state / marker).unlink()

    def test_a_copy_that_will_not_fit_is_refused_with_numbers(self):
        self.volume("desktop-linux", "trellis-data", {"trellis.sqlite3": "db"})
        (self.home / ".colima" / "_lima" / "_disks").mkdir(parents=True)
        for where, env in (("staging", {"STAGE_FREE_KIB": "500000", "DISK_DEV": "/dev/disk7"}),
                           ("the colima VM", {"VM_FREE_KIB": "500000"}),
                           ("the disk that holds the colima VM", {"DISK_DEV": "/dev/disk7", "DISK_FREE_KIB": "500000"})):
            with self.subTest(where):
                result = self.run_tool("copy-volume", "trellis-data", **env)
                self.assertEqual(result.returncode, 1)
                self.assertRegex(result.stderr, rf"not enough room in [^:]*{where}[^:]*: the copy needs 1\.0 GiB "
                                                r"and 0\.5 GiB is free")
                self.assertFalse((self.state / "vol-colima" / "trellis-data").exists())
                self.assertEqual(self.staged(), [])

    def test_on_one_disk_the_staged_file_and_colimas_growth_both_need_room(self):
        self.volume("desktop-linux", "trellis-data", {"blob": "x" * (8 << 20)})
        (self.home / ".colima" / "_lima" / "_disks").mkdir(parents=True)
        one_copy = str(1048576 + 8192 + 819 + 4096)  # 8 MiB with its margin, and a little; two copies do not fit
        result = self.run_tool("copy-volume", "trellis-data", STAGE_FREE_KIB=one_copy)
        self.assertEqual(result.returncode, 1)
        self.assertIn("which shares its disk with the colima VM", result.stderr)
        self.ok("copy-volume", "trellis-data", STAGE_FREE_KIB=one_copy, DISK_DEV="/dev/disk7")

    def test_a_copy_back_into_docker_desktop_needs_room_for_docker_raw_to_grow_too(self):
        # Docker.raw is a file on the Mac that grows as the VM writes; by default it sits under ~/Library.
        self.volume("colima", "trellis-data", {"blob": "x" * (8 << 20)})
        one_copy = str(1048576 + 8192 + 819 + 4096)
        back = ("copy-volume", "trellis-data", "--from", "colima", "--to", "docker-desktop")
        result = self.run_tool(*back, STAGE_FREE_KIB=one_copy)
        self.assertEqual(result.returncode, 1)
        self.assertIn("which shares its disk with the docker-desktop VM", result.stderr)
        self.assertFalse((self.state / "vol-desktop-linux" / "trellis-data").exists())
        # Docker Desktop's settings may move its data folder to another drive.
        settings = self.home / "Library" / "Group Containers" / "group.com.docker" / "settings-store.json"
        settings.parent.mkdir(parents=True)
        data_folder = self.home.parent / "Dock Drive" / "DockerDesktop"
        data_folder.mkdir(parents=True)
        settings.write_text(json.dumps({"DataFolder": str(data_folder)}))
        result = self.run_tool(*back, STAGE_FREE_KIB=one_copy, DISK_DEV="/dev/disk7", DISK_FREE_KIB="500000")
        self.assertEqual(result.returncode, 1)
        self.assertIn("not enough room in the disk that holds the docker-desktop VM", result.stderr)
        self.ok(*back, STAGE_FREE_KIB=one_copy, DISK_DEV="/dev/disk7")
        self.assertEqual((self.state / "vol-desktop-linux" / "trellis-data" / "blob").stat().st_size, 8 << 20)
        self.ok("use", "docker-desktop")
        self.assertEqual(self.context(), "desktop-linux")

    def test_an_image_comes_across(self):
        (self.state / "images-desktop-linux").write_text("ghcr.io/sage-is/ai-ui:3.2.0\t1.2GB\n")
        self.ok("copy-image", "ghcr.io/sage-is/ai-ui:3.2.0")
        self.assertEqual((self.state / "loaded-colima").read_text(), "IMAGE ghcr.io/sage-is/ai-ui:3.2.0")
        self.assertIn("save -o " + str(self.home / ".sage-is" / "staging" / "image-ghcr.io_sage-is_ai-ui_3.2.0.tar"),
                      " ".join(self.calls()))
        self.assertEqual(self.staged(), [])

    def test_an_image_that_will_not_fit_is_refused_with_numbers(self):
        # 369 MB, with a tenth and 1 GiB to spare, in a VM with half a GiB free.
        (self.state / "images-desktop-linux").write_text("yt:latest\t369MB\n")
        result = self.run_tool("copy-image", "yt:latest", VM_FREE_KIB="500000")
        self.assertEqual(result.returncode, 1)
        self.assertIn("not enough room in the colima VM: the copy needs 1.4 GiB and 0.5 GiB is free", result.stderr)
        self.assertFalse((self.state / "loaded-colima").exists())
        self.assertEqual(self.staged(), [])

    def test_colima_loads_and_saves_images_inside_its_vm_others_through_docker(self):
        staged = self.home / ".sage-is" / "staging" / "image-yt_latest.tar"
        (self.state / "images-desktop-linux").write_text("yt:latest\t369MB\n")
        self.ok("copy-image", "yt:latest")
        self.assertIn(f"colima ssh --profile default -- docker load -i {staged}", self.calls())
        self.ok("copy-image", "yt:latest", "--from", "colima", "--to", "docker-desktop")
        self.assertIn(f"colima ssh --profile default -- docker save -o {staged} yt:latest", self.calls())
        self.assertIn(f"docker desktop-linux load -i {staged}", self.calls())
        self.assertEqual((self.state / "loaded-desktop-linux").read_text(), "IMAGE yt:latest")


class Migrate(Mac):
    """One move from Docker Desktop to Colima, for a developer's whole Mac."""

    def setUp(self):
        super().setUp()
        self.install("Docker")
        self.running("desktop-linux")
        (self.state / "vm-default").touch()
        self.volume("desktop-linux", "sage-ai-data", {"webui.db": "chats"})
        self.volume("desktop-linux", "trellis-data", {"trellis.sqlite3": "new"})
        self.volume("desktop-linux", "0dc3aac9ee898fe8643bacba4b85364f4fba2c2663301716ceb92f08b70fc041", {"x": "1"})
        self.volume("desktop-linux", "buildx_buildkit_multi-arch-builder0_state", {"cache": "1"})
        # Copied over once, and Colima's app has written to it since.
        self.volume("colima", "trellis-data", {"trellis.sqlite3": "kept"})
        self.vouch("trellis-data")
        (self.state / "images-desktop-linux").write_text("yt:latest\t369MB\nalpine:3\t8MB\n")
        (self.state / "images-colima").write_text("alpine:3\t8MB\n")

    def vouch(self, name):
        """The record a copy of NAME from Docker Desktop leaves, for the source as it is now."""
        folder = self.state / "vol-desktop-linux" / name
        entries, kib = subprocess.run(["sh", "-c", f'find "{folder}" | wc -l; du -sk "{folder}" | cut -f1'],
                                      capture_output=True, text=True, check=True).stdout.split()
        record = self.home / ".sage-is" / "migrated"
        record.parent.mkdir(exist_ok=True)
        with record.open("a") as lines:
            lines.write(f"{name} docker-desktop colima {entries} {kib}\n")

    def colima_volumes(self):
        return sorted(p.name for p in (self.state / "vol-colima").iterdir())

    def test_every_named_volume_comes_across_and_docker_ends_on_colima(self):
        out = self.ok("migrate").stdout
        self.assertEqual((self.state / "vol-colima" / "sage-ai-data" / "webui.db").read_text(), "chats")
        self.assertEqual(self.colima_volumes(), ["sage-ai-data", "trellis-data"])
        self.assertEqual((self.state / "vol-colima" / "trellis-data" / "trellis.sqlite3").read_text(), "kept")
        self.assertIn("Skipping volume trellis-data", out)
        self.assertFalse(self.is_running("desktop-linux"))
        self.assertEqual(self.context(), "colima")
        self.assertEqual(self.saved(), "colima")
        self.assertIn("use docker-desktop", out)
        self.assertFalse((self.state / "loaded-colima").exists())

    def test_a_dry_run_lists_and_changes_nothing(self):
        self.running("colima")
        out = self.ok("migrate", "--dry-run", "--images").stdout
        self.assertIn("Would copy volume sage-ai-data", out)
        self.assertIn("Would copy image yt:latest (369MB)", out)
        self.assertNotIn("alpine:3", out)
        self.assertNotIn("Would start", out)
        self.assertEqual(self.colima_volumes(), ["trellis-data"])
        self.assertFalse((self.state / "loaded-colima").exists())
        self.assertEqual(self.started(), [])
        self.assertTrue(self.is_running("desktop-linux"))
        self.assertIsNone(self.saved())

    def test_a_dry_run_starts_nothing_and_says_what_a_real_run_would_start(self):
        out = self.ok("migrate", "--dry-run").stdout
        self.assertIn("Would start colima.", out)
        self.assertIn("Colima is not running, so nothing was checked against it", out)
        self.assertIn("Would copy volume sage-ai-data", out)
        self.assertEqual(self.started(), [])
        self.assertFalse(self.is_running("colima"))
        # No VM yet, and Docker Desktop quit: still nothing starts, and Lima's settings stay as they are.
        self.stub("krunkit", "exit 0")
        for marker in ("vm-default", "running-desktop-linux"):
            (self.state / marker).unlink()
        out = self.ok("migrate", "--dry-run").stdout
        self.assertIn("Would start docker-desktop.", out)
        self.assertIn("Would create the Colima VM default (krunkit): 12 GiB memory, 5 CPUs, 100 GiB disk.", out)
        self.assertIn("docker-desktop is not running, so nothing in it was listed.", out)
        self.assertEqual(self.started(), [])
        self.assertFalse((self.state / "vm-default").exists())
        self.assertFalse(self.override.exists())

    def test_images_come_across_only_what_colima_lacks(self):
        self.ok("migrate", "--images")
        self.assertEqual((self.state / "loaded-colima").read_text(), "IMAGE yt:latest")

    def test_an_image_that_will_not_fit_is_named_and_the_switch_goes_on(self):
        # Room for the small volume, not for the 369 MB image with its tenth and 1 GiB to spare.
        result = self.ok("migrate", "--images", VM_FREE_KIB="1200000")
        self.assertIn("Skipping image yt:latest: not enough room in the colima VM", result.stdout)
        self.assertIn("these images did not come across: yt:latest.", result.stderr)
        self.assertFalse((self.state / "loaded-colima").exists())
        self.assertEqual((self.state / "vol-colima" / "sage-ai-data" / "webui.db").read_text(), "chats")
        self.assertEqual(self.saved(), "colima")

    def test_an_untagged_image_is_skipped_and_one_that_fails_is_a_warning(self):
        (self.state / "images-desktop-linux").write_text(
            "yt:latest\t369MB\ncgr.dev/chainguard/wolfi-base:<none>\t33.3MB\nbroken:1\t5MB\nlate:1\t1MB\n")
        (self.state / "unsavable").write_text("broken:1\n")
        result = self.ok("migrate", "--images")
        self.assertIn("Skipping image cgr.dev/chainguard/wolfi-base: it has no tag", result.stdout)
        self.assertIn("these images did not come across: broken:1.", result.stderr)
        self.assertEqual((self.state / "loaded-colima").read_text(), "IMAGE yt:latestIMAGE late:1")
        self.assertEqual(self.saved(), "colima")

    def test_an_anonymous_volume_a_container_holds_is_named_as_it_is_left(self):
        # A container run without -v keeps its data in an anonymous volume; on Colima it starts with a new one.
        mail = "6530c7a7866705e725d6d3a33414c7c14484af18f7142150e821b2e009afbbda"
        self.volume("desktop-linux", mail, {"mail": "1"})
        self.container("desktop-linux", "trellis-imap", mail, stopped=True)
        out = self.ok("migrate").stdout
        self.assertIn("Leaving anonymous volume 6530c7a78667 (used by trellis-imap) in docker-desktop", out)
        self.assertNotIn("0dc3aac9ee89", out)  # nothing holds this one
        self.assertNotIn(mail, self.colima_volumes())

    def test_the_replace_line_names_both_sides_and_says_what_it_replaces(self):
        (self.home / ".sage-is" / "migrated").unlink()  # nothing vouches for Colima's trellis-data
        out = self.run_tool("migrate").stdout
        self.assertIn("To replace Colima's copy with docker-desktop's: sage-runtime copy-volume trellis-data "
                      "--from docker-desktop --to colima --force", out)
        self.install("OrbStack")
        self.running("orbstack")
        self.volume("orbstack", "trellis-data", {"trellis.sqlite3": "orb", "extra": "1"})
        out = self.run_tool("migrate", "--from", "orbstack").stdout
        self.assertIn("copy-volume trellis-data --from orbstack --to colima --force", out)

    def test_an_empty_source_offers_no_replace_line(self):
        (self.state / "vol-desktop-linux" / "trellis-data" / "trellis.sqlite3").unlink()
        out = self.ok("migrate").stdout
        self.assertIn("Skipping volume trellis-data: Colima has one with data, and docker-desktop's is empty.", out)
        self.assertNotIn("copy-volume trellis-data", out)

    def test_a_half_made_copy_in_colima_holds_the_switch_back(self):
        uploads = self.state / "vol-desktop-linux" / "sage-ai-data" / "uploads"
        uploads.mkdir()
        (uploads / "a.pdf").write_text("pdf")
        self.volume("colima", "sage-ai-data", {"webui.db": "chats"})
        result = self.run_tool("migrate")
        self.assertEqual(result.returncode, 1)
        self.assertRegex(result.stdout, r"Volume sage-ai-data in Colima holds data no recorded copy vouches for: "
                                        r"4 files and folders \(\d+ KiB\) in docker-desktop, 2 \(\d+ KiB\) in Colima\.")
        self.assertIn("copy-volume sage-ai-data --from docker-desktop --to colima --force", result.stdout)
        self.assertIn("not switching to Colima while these volumes may differ: sage-ai-data.", result.stderr)
        self.assertIn("switch: sage-runtime use colima", result.stderr)
        self.assertFalse((self.state / "vol-colima" / "sage-ai-data" / "uploads").exists())
        self.assertTrue(self.is_running("desktop-linux"))
        self.assertIsNone(self.saved())

    def test_as_many_files_without_a_record_still_hold_the_switch_back(self):
        # A trial run's database, while the real one grew on Docker Desktop, and a copy cut off inside its last file.
        self.volume("desktop-linux", "crm-data", {"crm.sqlite3": "x" * (1 << 20), "media/a.pdf": "pdf"})
        self.volume("colima", "crm-data", {"crm.sqlite3": "x" * 1024, "media/a.pdf": "pdf"})
        self.volume("desktop-linux", "chat-data", {"webui.db": "x" * (1 << 20)})
        self.volume("colima", "chat-data", {"webui.db": "x" * 1024})
        result = self.run_tool("migrate")
        self.assertEqual(result.returncode, 1)
        for name in ("crm-data", "chat-data"):
            self.assertIn(f"Volume {name} in Colima holds data no recorded copy vouches for", result.stdout)
        self.assertIn("not switching to Colima while these volumes may differ: chat-data crm-data.", result.stderr)
        self.assertEqual((self.state / "vol-colima" / "crm-data" / "crm.sqlite3").stat().st_size, 1024)
        self.assertTrue(self.is_running("desktop-linux"))
        self.assertIsNone(self.saved())

    def test_a_recorded_copy_is_skipped_until_its_source_changes(self):
        self.ok("migrate")
        out = self.ok("migrate").stdout
        self.assertIn("Skipping volume sage-ai-data: copied from docker-desktop before, and its count and size "
                      "there have not changed.", out)
        # A week more work back on Docker Desktop: the database grows, the file count stays.
        (self.state / "vol-desktop-linux" / "sage-ai-data" / "webui.db").write_text("chats" * 4000)
        result = self.run_tool("migrate")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Volume sage-ai-data changed in docker-desktop after it was copied.", result.stdout)
        self.assertEqual((self.state / "vol-colima" / "sage-ai-data" / "webui.db").read_text(), "chats")

    def test_nothing_is_copied_while_any_volume_is_in_use(self):
        self.container("desktop-linux", "trellis-crm", "trellis-data")
        result = self.run_tool("migrate")
        self.assertEqual(result.returncode, 1)
        self.assertIn("trellis-data: trellis-crm (docker-desktop)", result.stderr)
        self.assertIn("so nothing was copied", result.stderr)
        self.assertEqual(self.colima_volumes(), ["trellis-data"])
        self.assertIsNone(self.saved())

    def test_in_colima_only_the_volumes_it_would_copy_count(self):
        self.container("colima", "trellis-crm", "trellis-data")
        self.ok("migrate")
        self.volume("desktop-linux", "uploads", {"a.pdf": "pdf"})
        self.container("colima", "web", "uploads")
        result = self.run_tool("migrate")
        self.assertEqual(result.returncode, 1)
        self.assertIn("uploads: web (colima)", result.stderr)

    def test_a_probe_that_fails_stops_the_move(self):
        (self.state / "fail-probe-colima").touch()
        result = self.run_tool("migrate")
        self.assertEqual(result.returncode, 1)
        self.assertIn("could not look inside volume trellis-data in colima", result.stderr)
        self.assertEqual((self.state / "vol-colima" / "trellis-data" / "trellis.sqlite3").read_text(), "kept")
        self.assertEqual(self.colima_volumes(), ["trellis-data"])
        self.assertIsNone(self.saved())

    def test_colima_as_the_source_and_unknown_options_are_refused(self):
        result = self.run_tool("migrate", "--from", "colima")
        self.assertEqual(result.returncode, 1)
        self.assertIn("name the runtime it comes from", result.stderr)
        result = self.run_tool("migrate", "--all")
        self.assertEqual(result.returncode, 1)
        self.assertIn("unknown option --all", result.stderr)


class Convert(Mac):
    """A vz Colima VM, made before krunkit was the default, becomes a krunkit VM on the same data disk."""

    def setUp(self):
        super().setUp()
        self.stub("krunkit", "exit 0")
        self.colima("start", "--vm-type", "vz", "--vz-rosetta", "--memory", "8", "--cpu", "4", "--disk", "60")
        self.lima_yaml = self.home / ".colima" / "_lima" / "colima" / "lima.yaml"
        self.datadisk = self.home / ".colima" / "_lima" / "_disks" / "colima" / "datadisk"
        self.record = self.home / ".sage-is" / "convert"
        # colima delete takes these with the VM: the mounts come across, a copy keeps the rest.
        self.settings = self.home / ".colima" / "default" / "colima.yaml"
        self.settings.parent.mkdir(parents=True)
        self.settings.write_text("cpu: 4\ndocker:\n  insecure-registries: [registry.local:5000]\nvmType: vz\n"
                                 "mounts:\n  - location: \"/Volumes/Data Drive\"\n    writable: true\n"
                                 "  - location: ~/ro\n    writable: false\nenv: {}\n")
        self.volume("colima", "sage-ai-data", {"webui.db": "chats"})
        self.volume("colima", "trellis-data", {"trellis.sqlite3": "crm"})
        self.images = ("ghcr.io/sage-is/ai-ui:3.2.0", "alpine:3")
        (self.state / "images-colima").write_text("".join(f"{ref}\t8MB\n" for ref in self.images))
        for name, policy in (("registry", "unless-stopped"), ("sage-ai", "always"), ("tunnel", "no"),
                             ("worker", "on-failure")):
            self.container("colima", name, f"{name}-data", policy=policy)
        self.krunkit_start = ("colima start --profile default --vm-type krunkit --mount-type virtiofs --mount-inotify "
                              f"--memory 8 --cpu 4 --disk 60 --mount /Volumes/Data Drive:w --mount {self.home}/ro")
        (self.state / "calls").unlink()  # from here on, only the tool's calls

    def steps(self):
        """The calls that stop, delete or start a Colima VM."""
        return [c for c in self.calls() if c.startswith(("colima stop", "colima delete", "colima start"))]

    def by_hand(self):
        return [c for c in self.calls() if c.startswith("docker colima start")]

    def volumes(self):
        return sorted(p.name for p in (self.state / "vol-colima").iterdir())

    def test_the_plan_names_what_changes_and_changes_nothing(self):
        for args in ((), ("--dry-run",), ("--dry-run", "--yes")):
            with self.subTest(args):
                out = self.ok("convert", *args).stdout
                self.assertIn("The Colima VM default is vz: 8 GiB memory, 4 CPUs, 60 GiB disk.\n"
                              "convert makes it krunkit on the same data disk: 8 GiB memory, 4 CPUs, 60 GiB disk.\n"
                              f"It keeps the VM's mounts: --mount /Volumes/Data\\ Drive:w --mount {self.home}/ro\n"
                              f"colima delete also removes the VM's Colima settings, {self.settings}.", out)
                self.assertIn(f"First it adds the krunkit settings to Lima's override file, which every Colima VM "
                              f"reads: {self.override}", out)
                self.assertIn("Running containers it stops: registry sage-ai tunnel worker\n"
                              "It starts these again by hand, since Docker will not: tunnel worker\n"
                              "Dry run: nothing changed. Convert: sage-runtime convert --yes", out)
                self.assertEqual(self.steps(), [])
                self.assertFalse(self.override.exists())
                self.assertEqual(self.lima_yaml.read_text(), "vmType: vz\n")
                self.assertFalse(self.record.exists())

    def test_yes_makes_a_krunkit_vm_on_the_same_disk_with_every_volume_image_and_mount(self):
        settings = self.settings.read_text()
        out = self.ok("convert", "--yes", SAGE_RUNTIME_DISK="100").stdout  # the data disk keeps its size
        self.assertEqual(self.steps(), ["colima stop", "colima delete --force", self.krunkit_start])
        self.assertEqual((self.state / "mounts-default").read_text(), f"/Volumes/Data Drive:w\n{self.home}/ro\n")
        self.assertIn("Recorded 2 volumes and 2 images.", out)
        self.assertEqual(self.lima_yaml.read_text(), "vmType: krunkit\n")
        self.assertEqual(self.override.read_text(), VIRTIOFS + DATA_DISK)
        self.assertEqual(self.volumes(), ["sage-ai-data", "trellis-data"])
        self.assertEqual((self.state / "vol-colima" / "sage-ai-data" / "webui.db").read_text(), "chats")
        # Docker brings back what its restart policy says; convert starts the rest.
        self.assertEqual(self.by_hand(), ["docker colima start tunnel", "docker colima start worker"])
        self.assertEqual(sorted(p.name for p in (self.state / "run-colima").iterdir()),
                         ["registry", "sage-ai", "tunnel", "worker"])
        self.assertEqual(self.context(), "colima")
        self.assertFalse(self.record.exists())
        self.assertFalse(self.settings.exists())
        copies = list((self.home / ".sage-is").glob("colima-default.*.yaml"))
        self.assertEqual([copy.read_text() for copy in copies], [settings])
        self.assertIn("The Colima VM default is krunkit now, with every volume and image it had.", out)

    def test_the_environment_may_size_the_krunkit_vm_but_never_its_disk(self):
        self.ok("convert", "--yes", SAGE_RUNTIME_MEMORY="12", SAGE_RUNTIME_CPU="6", SAGE_RUNTIME_DISK="100")
        self.assertIn("--memory 12 --cpu 6 --disk 60 ", self.steps()[-1])

    def test_a_krunkit_vm_is_left_as_it_is_and_no_vm_points_at_use(self):
        self.lima_yaml.write_text("vmType: krunkit\n")
        self.assertIn("The Colima VM default is a krunkit VM already.", self.ok("convert", "--yes").stdout)
        self.assertEqual(self.steps(), [])
        self.colima("delete", "--force")
        result = self.run_tool("convert", "--yes")
        self.assertEqual(result.returncode, 1)
        self.assertIn("there is no Colima VM to convert. Make a krunkit one: sage-runtime use colima", result.stderr)
        self.assertEqual(self.steps(), ["colima delete --force"])

    def test_intel_and_a_mac_without_krunkit_keep_the_vm(self):
        for env, message in (({"MAC_ARCH": "x86_64"}, "krunkit runs only on Apple Silicon"),
                             ({}, f"krunkit is not installed. Get it: {KRUNKIT_INSTALL}")):
            with self.subTest(message):
                if not env:
                    (self.bin / "krunkit").unlink()
                result = self.run_tool("convert", "--yes", **env)
                self.assertEqual(result.returncode, 1)
                self.assertIn(message, result.stderr)
                self.assertEqual(self.steps(), [])
                self.assertEqual(self.lima_yaml.read_text(), "vmType: vz\n")

    def test_two_runtimes_on_the_shared_folder_are_refused(self):
        self.install("Docker")
        self.running("desktop-linux")
        shared = self.home / "SageData" / "sprig-registry"
        self.container("colima", "local-registry", shared)
        self.container("desktop-linux", "old-registry", shared)
        for args in (("--dry-run",), ("--yes",)):
            with self.subTest(args):
                result = self.run_tool("convert", *args)
                self.assertEqual(result.returncode, 1)
                self.assertIn("lose data", result.stderr)
                self.assertEqual(self.steps(), [])

    def test_a_vm_that_does_not_show_dockers_data_on_its_data_disk_is_never_deleted(self):
        # colima delete takes the root disk: Docker's data there would go with it.
        no_answer = "could not ask the Colima VM default where Docker keeps its data"
        for fault, stopped, message in (
                ("one-disk", False, "has no data disk"), ("one-disk", True, "has no data disk"),
                ("fail-ssh", False, no_answer), ("fail-ssh", True, no_answer), ("no-findmnt", False, no_answer),
                ("root-disk-colima", False, "keeps data in /var/lib/docker, which is not on its data disk where a "
                                           "krunkit VM looks"),
                # Docker 29's containerd store keeps images in containerd's folder.
                ("root-containerd", False, "keeps data in /var/lib/containerd, which"),
                ("root-containerd", True, "keeps data in /var/lib/containerd, which"),
                # A data-root in the VM's Colima settings, which a new VM does not get.
                ("data-root", False, "keeps data in /mnt/lima-colima/elsewhere, which")):
            with self.subTest(fault=fault, stopped=stopped):
                if stopped:
                    self.colima("stop")
                (self.state / fault).write_text("/mnt/lima-colima/elsewhere\n" if fault == "data-root" else "")
                if fault == "one-disk":  # a VM made before Colima had a data disk
                    self.datadisk.unlink()
                (self.state / "calls").unlink(missing_ok=True)
                result = self.run_tool("convert", "--yes")
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn(message, result.stderr)
                self.assertIn("It stays vz; nothing changed.", result.stderr)
                self.assertNotIn("colima delete", " ".join(self.calls()))
                self.assertEqual(self.is_running("colima"), not stopped)
                self.assertEqual(self.volumes(), ["sage-ai-data", "trellis-data"])
                self.assertEqual(self.lima_yaml.read_text(), "vmType: vz\n")
                (self.state / fault).unlink()
                self.datadisk.touch()
                if stopped:
                    self.colima("start")

    def test_data_in_folders_of_the_vms_root_disk_keeps_the_vm_as_it_is(self):
        # Docker makes /srv/pg inside the VM for docker run -v /srv/pg:..., on the root disk colima delete takes.
        # The volume names and image IDs stay the same, so the comparison after could not see the loss.
        self.container("colima", "pg", "/srv/pg", "pg-conf", stopped=True)
        self.volume("colima", "opt-data", {"x": "1"})
        (self.state / "device-colima").mkdir()
        (self.state / "device-colima" / "opt-data").write_text("/opt/x\n")
        for args in ((), ("--yes",)):
            with self.subTest(args):
                result = self.run_tool("convert", *args)
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn("these keep data in folders of the Colima VM's root disk, which colima delete takes:\n"
                              "  pg: /srv/pg\n  volume opt-data: /opt/x\n", result.stderr)
                self.assertIn("It stays vz; nothing changed.", result.stderr)
                self.assertEqual(self.steps(), [])
                self.assertEqual(self.lima_yaml.read_text(), "vmType: vz\n")
        # The Mac's folders, Docker's own, /run, and the system's folders hold no app data the delete takes.
        (self.state / "stopped-colima" / "pg").unlink()
        (self.state / "device-colima" / "opt-data").write_text(f"{self.home}/opt-data\n")
        self.container("colima", "agent", self.home / "proj", "/Volumes/Data/x", "/var/run/docker.sock",
                       "/var/lib/docker/containers", "/etc/localtime", "/var/log", "/usr/share/zoneinfo",
                       stopped=True)
        self.assertIn("krunkit now", self.ok("convert", "--yes").stdout)

    def test_mounts_in_either_block_form_come_across_with_their_mount_points(self):
        # Colima indents the list; a hand edit may not. mountPoint goes as --mount PATH:MOUNTPOINT[:w].
        self.settings.write_text("cpu: 4\nmounts:\n- location: ~/proj\n  mountPoint: /proj\n  writable: true\n"
                                 "# - location: ~/secrets\n\n- location: '/Volumes/Data'  # the drive\n"
                                 "  writable: false\nenv: {}\n")
        self.assertIn(f"It keeps the VM's mounts: --mount {self.home}/proj:/proj:w --mount /Volumes/Data\n",
                      self.ok("convert").stdout)
        self.ok("convert", "--yes")
        self.assertEqual((self.state / "mounts-default").read_text(), f"{self.home}/proj:/proj:w\n/Volumes/Data\n")
        self.assertEqual(self.lima_yaml.read_text(), "vmType: krunkit\n")

    def test_mounts_it_cannot_carry_across_keep_the_vm_as_it_is(self):
        for mounts in ("mounts: [{location: ~/proj, writable: true}]\n",
                       "mounts:\n  - location: ~/proj\n    readOnly: true\n",
                       "mounts:\n  - writable: true\n    location: ~/proj\n",
                       "mounts:\n  - location: ~/proj\n    mountPoint: proj\n"):
            with self.subTest(mounts):
                self.settings.write_text(mounts)
                for args in ((), ("--yes",)):
                    result = self.run_tool("convert", *args)
                    self.assertEqual(result.returncode, 1)
                    self.assertIn(f"convert cannot carry these lines of the VM's mounts in {self.settings} across:\n",
                                  result.stderr)
                    self.assertIn("It stays vz; nothing changed.", result.stderr)
                self.assertEqual(self.steps(), [])
                self.assertFalse(self.override.exists())
                self.assertEqual(self.lima_yaml.read_text(), "vmType: vz\n")

    def test_containers_started_with_rm_go_as_they_stop_and_are_not_counted_lost(self):
        # ai-ui try runs sage-try with --rm; an image that declares a VOLUME gives it an anonymous one.
        anonymous = "a" * 64
        self.volume("colima", anonymous, {"pg": "scratch"})
        self.volume("colima", "sage-try-data", {"webui.db": "try"})
        self.container("colima", "sage-try", "sage-try-data", anonymous, rm=True)
        self.assertIn("Running containers it stops: registry sage-ai sage-try tunnel worker\n"
                      "It starts these again by hand, since Docker will not: tunnel worker\n"
                      "Docker removes these as they stop, with their anonymous volumes, since they ran with --rm: "
                      "sage-try\n", self.ok("convert").stdout)
        out = self.ok("convert", "--yes").stdout
        self.assertIn("Recorded 3 volumes and 2 images.", out)
        self.assertEqual(self.volumes(), ["sage-ai-data", "sage-try-data", "trellis-data"])
        self.assertEqual(self.by_hand(), ["docker colima start tunnel", "docker colima start worker"])
        self.assertIn("krunkit now", out)
        self.assertFalse(self.record.exists())

    def test_a_failed_krunkit_start_prints_the_way_on_and_back_and_a_rerun_finishes(self):
        (self.state / "fail-start-krunkit").touch()
        result = self.run_tool("convert", "--yes")
        self.assertEqual(result.returncode, 1)
        self.assertIn("so the data disk keeps the vz VM's images and volumes. Once the krunkit VM is mended, "
                      "finish the conversion: sage-runtime convert --yes\n"
                      "Or go back to vz on that disk:\n"
                      "  colima delete --force --profile default\n"
                      "  colima start --profile default --vm-type vz --vz-rosetta --mount-type virtiofs "
                      "--mount-inotify --memory 8 --cpu 4 --disk 60 --mount /Volumes/Data\\ Drive:w "
                      f"--mount {self.home}/ro\n"
                      "Then start these containers again: docker --context colima start tunnel worker\n", result.stderr)
        self.assertRegex(result.stderr, r"The vz VM's Colima settings: \S+/\.sage-is/colima-default\.\d{8}-\d{6}\.yaml")
        self.assertTrue(self.record.exists())
        # A dry run says what is left; the rerun makes the krunkit VM with the vz VM's size and mounts.
        (self.state / "fail-start-krunkit").unlink()
        (self.state / "calls").unlink()
        out = self.ok("convert").stdout
        self.assertIn("stopped partway, after colima delete", out)
        self.assertIn("convert makes the krunkit VM on the same data disk: 8 GiB memory, 4 CPUs, 60 GiB disk.\n"
                      "It compares 2 volumes and 2 images with the vz VM's.\n"
                      "It starts these again by hand: tunnel worker\n"
                      "Dry run: nothing changed. Finish: sage-runtime convert --yes", out)
        self.assertEqual(self.steps(), [])
        out = self.ok("convert", "--yes").stdout
        self.assertEqual(self.steps(), [self.krunkit_start])
        self.assertEqual((self.state / "mounts-default").read_text(), f"/Volumes/Data Drive:w\n{self.home}/ro\n")
        self.assertEqual(self.by_hand(), ["docker colima start tunnel", "docker colima start worker"])
        self.assertIn("krunkit now", out)
        self.assertFalse(self.record.exists())

    def test_docker_on_the_root_disk_after_the_delete_stops_and_a_restart_and_rerun_finish(self):
        (self.state / "fail-mount").touch()  # the boot misses the data disk (abiosoft/colima#1614)
        result = self.run_tool("convert", "--yes")
        self.assertEqual(result.returncode, 1)
        self.assertIn("runs on the VM's root disk, not its data disk", result.stderr)
        self.assertIn("finish the conversion: sage-runtime convert --yes", result.stderr)
        self.assertIn("  colima delete --force --profile default\n", result.stderr)
        self.assertEqual(self.by_hand(), [])
        self.assertTrue(self.record.exists())
        (self.state / "fail-mount").unlink()
        for step in ("stop", "start"):  # colima restart
            self.colima(step)
        out = self.ok("convert", "--yes").stdout
        self.assertIn("It compares 2 volumes and 2 images with the vz VM's.", out)
        self.assertIn("krunkit now", out)
        self.assertEqual(self.by_hand(), ["docker colima start tunnel", "docker colima start worker"])
        self.assertFalse(self.record.exists())

    def test_a_krunkit_vm_that_lacks_the_data_names_it_and_the_way_back(self):
        # Docker on the root disk, and a VM that cannot be asked: only the comparison sees it.
        for fault in ("fail-mount", "fail-ssh-krunkit"):
            (self.state / fault).touch()
        result = self.run_tool("convert", "--yes")
        self.assertEqual(result.returncode, 1)
        ids = sorted(hashlib.sha256(ref.encode()).hexdigest()[:12] for ref in self.images)
        self.assertIn("the krunkit VM lacks what the vz VM held:\n  volumes: sage-ai-data trellis-data\n"
                      f"  images: {' '.join(ids)}\n", result.stderr)
        self.assertIn("  colima delete --force --profile default\n", result.stderr)
        self.assertEqual(self.by_hand(), [])
        self.assertTrue(self.record.exists())

    def test_an_override_a_krunkit_vm_cannot_use_is_refused_before_anything_starts_or_stops(self):
        self.override.parent.mkdir(parents=True)
        for held in ("provision:\n  - mode: system\n    script: echo hi\n", "mountType: 9p\n"):
            for stopped in (False, True):
                with self.subTest(held=held, stopped=stopped):
                    self.override.write_text(held)
                    if stopped:
                        self.colima("stop")
                    (self.state / "calls").unlink(missing_ok=True)
                    result = self.run_tool("convert", "--yes")
                    self.assertEqual(result.returncode, 1)
                    self.assertIn("Add what it lacks, then run this again. The vz VM is as it was.", result.stderr)
                    self.assertEqual(self.steps(), [])
                    self.assertEqual(self.is_running("colima"), not stopped)
                    if stopped:
                        self.colima("start")

    def test_a_stopped_vm_is_started_for_the_record_converted_and_stopped_again(self):
        self.colima("stop")
        (self.state / "calls").unlink()
        out = self.ok("convert", "--yes").stdout
        self.assertIn("The VM is stopped. convert starts it", out)
        self.assertEqual(self.steps(), ["colima start", "colima stop", "colima delete --force", self.krunkit_start,
                                        "colima stop"])
        self.assertFalse(self.is_running("colima"))
        self.assertEqual(self.lima_yaml.read_text(), "vmType: krunkit\n")
        self.assertEqual(self.volumes(), ["sage-ai-data", "trellis-data"])
        self.assertFalse(self.record.exists())

    def test_dockers_context_goes_back_to_the_runtime_in_use(self):
        self.install("Docker")
        self.running("desktop-linux")
        (self.home / ".sage-is").mkdir()
        (self.home / ".sage-is" / "runtime").write_text("docker-desktop\n")
        (self.state / "current-context").write_text("desktop-linux\n")
        self.ok("convert", "--yes")
        self.assertEqual(self.context(), "desktop-linux")
        self.assertEqual(self.lima_yaml.read_text(), "vmType: krunkit\n")

    def test_a_container_that_will_not_start_is_named_without_the_way_back(self):
        (self.state / "unstartable").touch()
        result = self.run_tool("convert", "--yes")
        self.assertEqual(result.returncode, 1)
        self.assertIn("these containers did not start again: tunnel worker.", result.stderr)
        self.assertNotIn("colima delete --force --profile default", result.stderr)
        self.assertEqual(self.lima_yaml.read_text(), "vmType: krunkit\n")
        self.assertFalse(self.record.exists())

    def test_the_hint_names_convert_only_where_it_can_run(self):
        hint = ("The Colima VM default is still vz. Move it to krunkit, keeping its images and volumes: "
                "sage-runtime convert")
        self.assertIn(hint, self.ok("use", "colima").stdout)
        self.install("Docker")
        self.assertIn(hint, self.ok("migrate", "--dry-run").stdout)
        self.assertNotIn(hint, self.ok("use", "colima", MAC_ARCH="x86_64").stdout)
        (self.bin / "krunkit").unlink()
        self.assertNotIn(hint, self.ok("use", "colima").stdout)
        self.stub("krunkit", "exit 0")
        self.lima_yaml.write_text("vmType: krunkit\n")
        self.assertNotIn("convert", self.ok("use", "colima").stdout)
        self.record.write_text("size 4 8 60\n")  # a conversion cut short
        self.assertIn("A conversion of the Colima VM default to krunkit stopped partway. "
                      "Finish it: sage-runtime convert --yes", self.ok("use", "colima").stdout)

    def test_an_unknown_option_is_refused(self):
        result = self.run_tool("convert", "--force")
        self.assertEqual(result.returncode, 1)
        self.assertIn("unknown option --force", result.stderr)


class BuildVM(Mac):
    """A second Colima VM with Rosetta for amd64 builds; everyday work stays on the first."""

    def setUp(self):
        super().setUp()
        self.stub("krunkit", "exit 0")
        (self.state / "vm-default").touch()
        self.ok("use", "colima")

    def test_the_first_run_makes_a_vz_vm_with_rosetta_and_leaves_docker_where_it_was(self):
        out = self.ok("build-vm").stdout
        self.assertIn("colima start --profile build --vm-type vz --vz-rosetta --activate=false --mount-type virtiofs "
                      "--mount-inotify --memory 10 --cpu 5 --disk 100", self.calls())
        self.assertTrue(self.is_running("colima-build"))
        self.assertEqual(self.context(), "colima")
        self.assertIn("DOCKER_CONTEXT=colima-build make <target>", out)

    def test_on_an_intel_mac_it_is_vz_without_rosetta(self):
        self.ok("build-vm", MAC_ARCH="x86_64")
        self.assertIn("colima start --profile build --vm-type vz --activate=false --mount-type virtiofs "
                      "--mount-inotify --memory 10 --cpu 5 --disk 100", self.calls())

    def test_it_has_room_for_ai_uis_multi_arch_release_on_any_mac(self):
        # AI-UI's release gate wants 8 GiB in docker info, and a VM's kernel keeps some of what it is given.
        for ram, memory in (("16", 9), ("32", 10), ("64", 12)):
            with self.subTest(ram=ram):
                for marker in ("vm-build", "running-colima-build"):
                    (self.state / marker).unlink(missing_ok=True)
                self.ok("build-vm", MAC_RAM_GIB=ram)
                starts = [c for c in self.calls() if c.startswith("colima start --profile build")]
                self.assertIn(f"--memory {memory} ", starts[-1])

    def test_stop_stops_only_it_and_the_next_run_starts_it_as_it_is(self):
        self.ok("build-vm")
        self.ok("build-vm", "--stop")
        self.assertFalse(self.is_running("colima-build"))
        self.assertTrue(self.is_running("colima"))
        self.ok("build-vm")
        starts = [c for c in self.calls() if c.startswith("colima start --profile build")]
        self.assertEqual(starts[-1], "colima start --profile build --activate=false")
        self.assertEqual(self.context(), "colima")

    def test_a_build_vm_made_by_hand_does_not_take_dockers_context_either(self):
        (self.state / "vm-build").touch()  # made with a plain colima start --profile build
        self.ok("build-vm")
        self.assertTrue(self.is_running("colima-build"))
        self.assertEqual(self.context(), "colima")

    def test_a_shell_that_exports_the_build_context_moves_nothing(self):
        # Build shells export DOCKER_CONTEXT=colima-build. Colima, stopping the
        # VM, removes that context; docker's saved one must not go with it.
        for args in (("build-vm",), ("build-vm", "--stop")):
            with self.subTest(args=args):
                self.ok(*args, DOCKER_CONTEXT="colima-build")
                self.assertEqual(self.context(), "colima")

    def test_status_shows_it(self):
        self.assertNotIn("build VM", self.ok("status").stdout)
        self.ok("build-vm")
        self.assertRegex(self.ok("status").stdout, r"build VM\s+running; stop it")
        self.ok("build-vm", "--stop")
        self.assertRegex(self.ok("status").stdout, r"build VM\s+stopped")

    def test_colima_not_its_docker_says_whether_it_runs(self):
        self.ok("build-vm")
        (self.state / "fail-info-colima-build").touch()  # the VM runs; its docker does not answer
        self.assertRegex(self.ok("status").stdout, r"build VM\s+running; stop it")
        self.ok("build-vm", "--stop")
        self.assertIn("colima stop --profile build", self.calls())
        self.assertFalse(self.is_running("colima-build"))

    def test_a_stopped_vms_builder_is_not_called_stale(self):
        # Colima drops a VM's docker context as it stops, and makes it again at start.
        builders = self.home / ".docker" / "buildx" / "instances"
        builders.mkdir(parents=True)
        for name, endpoint in (("multi-arch-builder-colima-build", "colima-build"), ("gone", "colima-old")):
            (builders / name).write_text(json.dumps({"Name": name, "Nodes": [{"Endpoint": endpoint}]}))
        self.ok("build-vm")
        self.ok("build-vm", "--stop")
        out = self.ok("status").stdout
        self.assertNotIn("multi-arch-builder-colima-build points at", out)
        self.assertIn("docker buildx rm gone", out)

    def test_it_is_refused_while_it_and_a_runtime_both_use_the_shared_folder(self):
        # A registry on each VM would write ~/SageData from both and claim port 5000 twice.
        shared = self.home / "SageData" / "sprig-registry"
        self.container("colima", "sprig-registry", shared)
        self.container("colima-build", "registry", shared)  # restart policy always: back as the VM starts
        (self.state / "vm-build").touch()
        for running in (False, True):
            with self.subTest(running=running):
                if running:
                    self.running("colima-build")
                result = self.run_tool("build-vm")
                self.assertEqual(result.returncode, 1)
                self.assertIn(f"both colima-build (registry) and colima (sprig-registry) are using {self.home}/SageData. "
                              "Two runtimes writing there at once lose data. Stop one side first.", result.stderr)
                # It stops the build VM only when it started it.
                self.assertEqual("Stopping colima-build..." in result.stdout, not running)
                self.assertEqual(self.is_running("colima-build"), running)
                self.assertTrue(self.is_running("colima"))
                self.assertEqual(self.context(), "colima")
        (self.state / "run-colima" / "sprig-registry").unlink()
        self.ok("build-vm")

    def test_the_everyday_side_is_refused_too_while_the_build_vm_uses_the_shared_folder(self):
        # use never stops the build VM, so its registry would stay on ~/SageData beside the everyday one.
        shared = self.home / "SageData" / "sprig-registry"
        self.ok("build-vm")
        self.container("colima-build", "registry", shared)
        self.container("colima", "sprig-registry", shared)
        self.install("Docker")
        self.volume("desktop-linux", "trellis-data", {"f": "1"})
        for args in (("use", "colima"), ("use", "colima", "--keep-other"), ("copy-volume", "trellis-data")):
            with self.subTest(args):
                result = self.run_tool(*args)
                self.assertEqual(result.returncode, 1)
                self.assertIn(f"both colima (sprig-registry) and colima-build (registry) are using {self.home}/SageData",
                              result.stderr)
                self.assertTrue(self.is_running("colima-build"))
                self.assertFalse(self.is_running("desktop-linux"))
                self.assertFalse((self.state / "vol-colima" / "trellis-data").exists())
        self.ok("build-vm", "--stop")
        self.ok("use", "colima")
        self.ok("use", "docker-desktop")
        self.assertEqual(self.context(), "desktop-linux")

    def test_an_unknown_option_is_refused(self):
        result = self.run_tool("build-vm", "--now")
        self.assertEqual(result.returncode, 1)
        self.assertIn("unknown option --now", result.stderr)


class Basics(Mac):
    def test_version_and_help(self):
        self.assertRegex(self.ok("version").stdout.strip(), r"^sage-runtime \d+\.\d+\.\d+$")
        self.assertIn("copy-volume NAME", self.ok("--help").stdout)

    def test_help_says_how_to_get_krunkit_and_which_lima_file_it_changes(self):
        out = self.ok("--help", COLIMA_HOME=str(self.home / "colima")).stdout
        self.assertIn(KRUNKIT_INSTALL, out)
        self.assertIn(str(self.home / "colima" / "_lima" / "_config" / "override.yaml"), out)
        self.assertIn("so a QEMU Colima profile will not start", out)
        self.assertIn("mounts the VM's data disk", out)
        self.assertIn("colima ssh -- findmnt /var/lib/docker shows /dev/vdc1", out)

    def test_help_says_what_convert_keeps_and_what_it_never_deletes(self):
        out = " ".join(self.ok("--help").stdout.split())
        self.assertIn("convert [--dry-run] [--yes] Make the vz Colima VM a krunkit VM on the same data disk", out)
        self.assertIn("the same memory, CPUs, disk and mounts", out)
        self.assertIn("goes on only while the VM shows Docker's data on its data disk, and no container or volume "
                      "keeps data in a folder of the root disk", out)
        self.assertIn("deletes the VM but never its data disk (no --data)", out)
        self.assertIn("convert --yes finishes from the record", out)

    def test_an_option_without_its_runtime_says_how_to_use_it(self):
        for args in (("copy-volume", "x", "--to"), ("copy-image", "x", "--from"), ("migrate", "--from")):
            with self.subTest(args):
                result = self.run_tool(*args)
                self.assertEqual(result.returncode, 1)
                self.assertIn(f"{args[-1]} needs a runtime: one of colima docker-desktop orbstack", result.stderr)

    def test_no_docker_cli_says_how_to_get_it(self):
        (self.bin / "docker").unlink()
        result = self.run_tool("status")
        self.assertEqual(result.returncode, 1)
        self.assertIn("brew install docker", result.stderr)


class Formula(unittest.TestCase):
    """krunkit's own formula refuses Intel, so only Apple Silicon may depend on it."""

    def test_krunkit_is_an_apple_silicon_dependency_and_the_caveats_trust_its_tap_first(self):
        text = FORMULA.read_text()
        self.assertIn('\n  on_arm do\n    depends_on "libkrun/krun/krunkit"\n  end\n', text)
        self.assertEqual(text.count('depends_on "libkrun/krun/krunkit"'), 1)
        caveats = " ".join(text[text.index("def caveats"):].split())
        self.assertIn(KRUNKIT_TAP, caveats)
        self.assertNotIn("--formula", caveats)
        # migrate makes the Colima VM, and a VM keeps its type until convert makes it krunkit.
        self.assertLess(caveats.index(KRUNKIT_TAP), caveats.index("sage-runtime migrate"))
        self.assertLess(caveats.index(KRUNKIT_TAP), caveats.index("sage-runtime convert --yes"))
        self.assertIn("convert makes a vz VM krunkit on the same data disk", caveats)


class Docs(unittest.TestCase):
    """The man page and the README give the install lines the tool gives."""

    def test_they_trust_krunkits_tap_before_installing(self):
        self.assertIn(KRUNKIT_INSTALL, (TOOL.parent / "sage-runtime.1").read_text())

    def test_the_man_page_has_convert_and_the_files_it_keeps(self):
        page = (TOOL.parent / "sage-runtime.1").read_text()
        for line in (".It Cm convert Oo Fl -dry-run Oc Oo Fl -yes Oc", ".It Pa ~/.sage-is/convert",
                     ".It Pa ~/.sage-is/colima-default.YYYYMMDD-HHMMSS.yaml", "sage-runtime convert --yes"):
            self.assertIn(line + "\n", page)
        readme = (TOOL.parent / "README.md").read_text()
        section = readme[readme.index("\n## sage-runtime\n"):]
        section = section[:section.index("\n## ", 1)]
        self.assertRegex(section,
                         r"brew tap libkrun/krun && brew trust [^\n]*libkrun/krun && brew install sage-runtime")


if __name__ == "__main__":
    unittest.main()
