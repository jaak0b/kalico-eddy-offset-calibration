#!/usr/bin/env python3
# Copyright (C) 2026  Jakob
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import argparse
import contextlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent
PLUGIN_SRC = REPO_DIR / 'eddy_tool_calibration.py'
CASE_DIR = REPO_DIR / 'integration'

# Kalico's linuxprocess target builds with the host compiler, so no
# cross-toolchain is needed for the simulated MCU the cases run against.
MCU_TARGET = 'linuxprocess'
DICT_NAME = '%s.dict' % (MCU_TARGET,)

CASE_TIMEOUT = 600.0

# scripts/test_klippy.py:13. Kalico's harness gives klippy a log file of this
# name in the directory named by -t, unless it was asked for verbose output:
# then klippy has no log file and Python's fallback handler puts only warnings
# and worse on stderr, well above the level the markers below are logged at.
KLIPPY_LOG = '_test_.log'

# Klipper's gcode dispatcher catches CommandError and nothing else, so any
# other exception is logged with a traceback and shuts the printer down.
# Whatever the case expects, none of these may appear.
FORBIDDEN_ALWAYS = (
    'Traceback (most recent call last)',
    'Unhandled exception',
    'Internal error',
)

HOME_FIRST = 'Home the printer first.'

# The plugin reports this for the first press of a debug run, so it appears
# only once a probing move has run through the plugin's endstop object and
# returned a trigger position.
SWITCH_PRESSED = 'switch press 1 trigger (machine Z):'

# klippy logs this once every config section has been built and the connect
# handlers are running, so it is what says the plugin's section survived
# config load. The gcode of a case runs only after it.
CONNECTED = "Sending MCU 'mcu' printer configuration..."

CASES = (
    {
        'name': 'config load',
        'test': 'config_load.test',
        'require': (CONNECTED,),
        'forbid': (),
    },
    {
        'name': 'EDDY_LOCATE',
        'test': 'eddy_locate_unhomed.test',
        'require': (HOME_FIRST,),
        'forbid': ('Unknown command:"EDDY_LOCATE"',),
    },
    {
        'name': 'EDDY_CALIBRATE_Z',
        'test': 'eddy_calibrate_z_unhomed.test',
        'require': (HOME_FIRST,),
        'forbid': ('Unknown command:"EDDY_CALIBRATE_Z"',),
    },
    {
        'name': 'EDDY_CALIBRATE_Z homed',
        'test': 'eddy_calibrate_z_homed.test',
        'require': (SWITCH_PRESSED,),
        'forbid': (),
    },
    {
        'name': 'EDDY_CALIBRATE_OFFSET',
        'test': 'eddy_calibrate_offset_unhomed.test',
        'require': (HOME_FIRST,),
        'forbid': ('Unknown command:"EDDY_CALIBRATE_OFFSET"',),
    },
    {
        'name': 'EDDY_REPEATABILITY',
        'test': 'eddy_repeatability_unhomed.test',
        'require': (HOME_FIRST,),
        'forbid': ('Unknown command:"EDDY_REPEATABILITY"',),
    },
)


class Failure(Exception):
    pass


def report(line=''):
    sys.stdout.write(line + '\n')
    sys.stdout.flush()


def check_checkout(raw):
    kalico = Path(raw).expanduser().resolve()
    if not kalico.is_dir():
        raise Failure(
            "Kalico checkout not found: %s. Pass the directory holding "
            "Kalico's klippy/ and scripts/ directories." % (kalico,))
    needed = [
        kalico / 'klippy',
        kalico / 'scripts' / 'test_klippy.py',
        kalico / 'test' / 'configs' / ('%s.config' % (MCU_TARGET,)),
        kalico / 'Makefile',
    ]
    missing = [str(p) for p in needed if not p.exists()]
    if missing:
        raise Failure(
            "%s does not look like a Kalico checkout. Missing: %s."
            % (kalico, ", ".join(missing)))
    return kalico


def run_tool(command, cwd, env, what):
    try:
        proc = subprocess.run(
            command, cwd=str(cwd), env=env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    except OSError as e:
        raise Failure("%s could not be started: %s." % (what, e))
    if proc.returncode != 0:
        raise Failure(
            "%s failed with exit code %d.\n%s"
            % (what, proc.returncode, proc.stdout))
    return proc.stdout


def build_dictionary(kalico, build_dir, dictdir):
    """Build the MCU dictionary the cases run against from this checkout.

    The dictionary carries the command set of the firmware, so it has to come
    from the same checkout as klippy: a dictionary built elsewhere would test
    the plugin against a protocol that checkout does not speak.
    """
    source = kalico / 'test' / 'configs' / ('%s.config' % (MCU_TARGET,))
    build_dir.mkdir(parents=True, exist_ok=True)
    config = build_dir / '.config'
    shutil.copyfile(str(source), str(config))
    # The Makefile joins OUT with relative paths, so it has to end in a
    # separator, and it keeps the build out of the checkout's own out/.
    make = [
        'make', '-C', str(kalico),
        'OUT=%s%s' % (build_dir, os.sep),
        'KCONFIG_CONFIG=%s' % (config,),
    ]
    env = os.environ.copy()
    try:
        run_tool(make + ['olddefconfig'], kalico, env, 'The firmware configure')
        run_tool(make, kalico, env, 'The firmware build')
    except Failure as e:
        raise Failure(
            "%s\nThe %s firmware is built with make and the host C compiler. "
            "Install both, or point --dictdir at a directory that already "
            "holds %s." % (e, MCU_TARGET, DICT_NAME))
    built = build_dir / 'klipper.dict'
    if not built.is_file():
        raise Failure(
            "The firmware build produced no %s. Kalico's build writes the "
            "dictionary next to klipper.elf; check the build output above."
            % (built,))
    dictdir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(str(built), str(dictdir / DICT_NAME))


def build_chelper(kalico, env):
    """Compile klippy's C helper before any case runs.

    Kalico's own test/conftest.py calls chelper.get_ffi() at session start for
    the same reason: the first import compiles c_helper.so, and a compiler or
    dependency problem there would otherwise be reported as a failing case.
    """
    code = 'import klippy.chelper; klippy.chelper.get_ffi()'
    try:
        run_tool(
            [sys.executable, '-c', code], kalico, env,
            "Building klippy's C helper")
    except Failure as e:
        raise Failure(
            "%s\nklippy needs a C compiler and its Python dependencies "
            "(cffi, greenlet, Jinja2, numpy, pyserial). Install them into "
            "%s, the interpreter running this script."
            % (e, sys.executable))


def link_plugin(target):
    try:
        os.symlink(str(PLUGIN_SRC), str(target))
        return 'symlink'
    except OSError as symlink_error:
        try:
            shutil.copyfile(str(PLUGIN_SRC), str(target))
        except OSError as copy_error:
            raise Failure(
                "Could not install the plugin at %s. The symlink failed with "
                "%s and the copy failed with %s. Check the write permissions "
                "on that directory." % (target, symlink_error, copy_error))
        return 'copy'


@contextlib.contextmanager
def installed_plugin(kalico):
    plugins_dir = kalico / 'klippy' / 'plugins'
    target = plugins_dir / PLUGIN_SRC.name
    package_marker = plugins_dir / '__init__.py'
    created_dir = created_marker = False
    backup = None
    if not plugins_dir.is_dir():
        plugins_dir.mkdir(parents=True)
        created_dir = True
    if not package_marker.exists():
        package_marker.touch()
        created_marker = True
    already_installed = False
    if target.is_symlink() or target.exists():
        if target.resolve() == PLUGIN_SRC:
            already_installed = True
        else:
            backup = plugins_dir / (target.name + '.integration-backup')
            if backup.exists():
                raise Failure(
                    "%s already exists. A previous run left it behind: move "
                    "the file you want to keep back to %s and delete the "
                    "other one." % (backup, target))
            os.replace(str(target), str(backup))
    try:
        if already_installed:
            report('plugin: already installed at %s' % (target,))
        else:
            report('plugin: installed at %s by %s'
                   % (target, link_plugin(target)))
        yield
    finally:
        if not already_installed and (target.is_symlink() or target.exists()):
            target.unlink()
        if backup is not None:
            os.replace(str(backup), str(target))
        cache = plugins_dir / '__pycache__'
        if cache.is_dir():
            shutil.rmtree(str(cache), ignore_errors=True)
        if created_marker and package_marker.exists():
            package_marker.unlink()
        if created_dir:
            try:
                plugins_dir.rmdir()
            except OSError as e:
                report('note: %s was created by this run and could not be '
                       'removed: %s' % (plugins_dir, e))


def case_output(case_dir, harness_output):
    """Everything one case produced: what Kalico's harness printed, plus the
    klippy log it kept. That harness prints the log itself when a run defied
    the case's expectation, so it is appended only when it is not there yet.
    """
    output = harness_output or ''
    log_path = case_dir / KLIPPY_LOG
    if not log_path.is_file():
        return output + '\n%s was never written.\n' % (log_path,)
    log = log_path.read_text(errors='replace')
    if log in output:
        return output
    return output + log


def run_case(case, kalico, dictdir, workdir, env):
    """Run one case through Kalico's harness. Returns (problems, output)."""
    # klippy appends to its log file and the name above is the same for every
    # case, so each case runs in a directory of its own.
    case_dir = workdir / case['test']
    case_dir.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable, str(kalico / 'scripts' / 'test_klippy.py'),
        '-k', '-d', str(dictdir), '-t', '.', str(CASE_DIR / case['test']),
    ]
    try:
        proc = subprocess.run(
            command, cwd=str(case_dir), env=env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            timeout=CASE_TIMEOUT)
    except subprocess.TimeoutExpired as e:
        return (["the run did not finish within %.0f seconds"
                 % (CASE_TIMEOUT,)], case_output(case_dir, e.output))
    except OSError as e:
        return (["Kalico's harness could not be started: %s" % (e,)], '')
    output = case_output(case_dir, proc.stdout)
    problems = []
    if proc.returncode != 0:
        problems.append(
            "Kalico's harness reported failure, exit code %d"
            % (proc.returncode,))
    for marker in case['require']:
        if marker not in output:
            problems.append("the expected output %r never appeared" % (marker,))
    for marker in tuple(case['forbid']) + FORBIDDEN_ALWAYS:
        if marker in output:
            problems.append("the output %r appeared" % (marker,))
    return problems, output


def run_cases(kalico, dictdir, workdir, env, verbose):
    failed = 0
    for case in CASES:
        problems, output = run_case(case, kalico, dictdir, workdir, env)
        if problems:
            failed += 1
            report('case %s: FAILED' % (case['name'],))
            for problem in problems:
                report('    %s' % (problem,))
        else:
            report('case %s: passed' % (case['name'],))
        if problems or verbose:
            report('--- output of %s ---' % (case['test'],))
            report(output.rstrip())
            report('--- end of output ---')
    report()
    report('cases run: %d' % (len(CASES),))
    report('cases failed: %d' % (failed,))
    return failed


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Run the plugin's config section and its commands through "
            "Kalico's own regression harness against a Kalico checkout. This "
            "is a separate entry point from the unit tests, because it needs "
            "a checkout, a C compiler and a POSIX host."))
    parser.add_argument(
        'kalico', help="path to the Kalico checkout to test the plugin against")
    parser.add_argument(
        '--dictdir', default=None,
        help=("directory holding %s. It is built from the checkout when it is "
              "missing there. Without this option the dictionary is built into "
              "a temporary directory on every run." % (DICT_NAME,)))
    parser.add_argument(
        '--verbose', action='store_true',
        help="print the klippy output of every case, not only failing ones")
    args = parser.parse_args()

    if os.name != 'posix':
        raise Failure(
            "klippy runs on POSIX hosts only: it needs fork and the termios "
            "module. Run this script on the printer host, or in a Linux "
            "virtual machine or container with the Kalico checkout mounted.")
    if not PLUGIN_SRC.is_file():
        raise Failure(
            "cannot find %s. Run this script from its own repository."
            % (PLUGIN_SRC,))

    kalico = check_checkout(args.kalico)
    report('kalico checkout: %s' % (kalico,))
    report('python: %s' % (sys.executable,))

    env = os.environ.copy()
    existing = env.get('PYTHONPATH')
    env['PYTHONPATH'] = (
        str(kalico) if not existing else str(kalico) + os.pathsep + existing)

    with tempfile.TemporaryDirectory(prefix='eddy-integration-') as scratch:
        scratch = Path(scratch)
        if args.dictdir is None:
            dictdir = scratch / 'dict'
        else:
            dictdir = Path(args.dictdir).expanduser().resolve()
        if (dictdir / DICT_NAME).is_file():
            report('dictionary: %s, reused' % (dictdir / DICT_NAME,))
        else:
            build_dictionary(kalico, scratch / 'build', dictdir)
            report('dictionary: %s, built from this checkout'
                   % (dictdir / DICT_NAME,))
        build_chelper(kalico, env)
        workdir = scratch / 'run'
        workdir.mkdir()
        report()
        with installed_plugin(kalico):
            failed = run_cases(kalico, dictdir, workdir, env, args.verbose)
    return 1 if failed else 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Failure as error:
        sys.stderr.write('integration_test.py: %s\n' % (error,))
        sys.exit(2)
