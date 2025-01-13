#!/usr/bin/env python

# Taken from https://opendev.org/openstack/sunbeam-charms/src/commit/5b37e0a6919668f23b8c7b148717714889fd4381/repository.py

"""CLI tool to execute an action on any charm managed by this repository."""

import argparse
import glob
import logging
import os
import pathlib
import shutil
import subprocess
import tomllib
import fnmatch
import sys
import io
from threading import Thread
from dataclasses import dataclass
from collections.abc import Iterable
from typing import Any

import yaml

ROOT_DIR = pathlib.Path(__file__).parent
BUILD_PATH = ROOT_DIR / "_build"
PYPROJECT_FILE = "pyproject.toml"
CHARMCRAFT_FILE = "charmcraft.yaml"
EXTERNAL_LIB_DIR = ROOT_DIR / "external" / "lib"
CURRENT_DIRECTORY = pathlib.Path(__file__).parent.resolve()


logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


class RepositoryError(Exception):
    """Raise if the tool could not execute correctly."""


###############################################
# Utility functions
###############################################
@dataclass(init=False)
class BuildTool:
    path: str

    def __init__(self, tool: str) -> None:
        if not (tool_path := shutil.which(tool)):
            raise RepositoryError(f"Binary `{tool}` not installed or not in the PATH")

        logger.debug(f"Using {tool} from `{tool_path}`")

        self.path = tool_path

    def run_command(self, args: [str], *popenargs, **kwargs):
        def reader(pipe):
            with pipe:
                for line in pipe:
                    line.replace(
                        str(CURRENT_DIRECTORY / "_build"), str(CURRENT_DIRECTORY / "charms")
                    )
                    print(line, end="")

        kwargs["text"] = True
        args.insert(0, self.path)
        env = kwargs.pop("env", os.environ)
        env["COLOR"] = "1"
        with subprocess.Popen(args, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs) as process:
            Thread(target=reader, args=[process.stdout]).start()
            Thread(target=reader, args=[process.stderr]).start()
            return_code = process.wait()
        
        if return_code != 0:
            raise subprocess.CalledProcessError(returncode=return_code, cmd=args)


UV = BuildTool("uv")
CHARMCRAFT = BuildTool("charmcraft")


@dataclass(init=False)
class Charm:
    """Information used to build a charm."""

    metadata: dict[str, Any]
    path: pathlib.Path
    internal_libraries: list[str]
    templates: list[str]

    def __init__(self, charm: str) -> "Charm":
        """Load this charm from a path."""
        path = ROOT_DIR / "charms" / charm

        try:
            with (path / PYPROJECT_FILE).open(mode="rb") as f:
                project = tomllib.load(f)
        except OSError:
            raise RepositoryError(f"Failed to read file `{path / PYPROJECT_FILE}`.")

        try:
            with (path / CHARMCRAFT_FILE).open(mode="rb") as f:
                metadata = dict(yaml.safe_load(f))
        except OSError:
            raise RepositoryError(f"Failed to read file `{path / CHARMCRAFT_FILE}`.")

        try:
            internal_libraries = project["tool"]["repository"]["internal-libraries"]
        except KeyError:
            internal_libraries = []

        try:
            templates = project["tool"]["repository"]["templates"]
        except KeyError:
            templates = []

        self.path = path
        self.internal_libraries = internal_libraries
        self.templates = templates
        self.metadata = metadata

    @property
    def name(self) -> str:
        """Get the name of the charm."""
        return str(self.path.name)

    @property
    def build_path(self) -> pathlib.Path:
        """Get the directory path that the staged charm must have on the output build directory."""
        return BUILD_PATH / self.path.name

    @property
    def charm_path(self) -> pathlib.Path:
        """Get the file path that the built charm must have on the output build directory."""
        return BUILD_PATH / f"{self.path.name}.charm"


def _library_to_path(library: str) -> pathlib.Path:
    split = library.split(".")
    if len(split) != 4:
        raise RepositoryError(f"Invalid library: {library}")
    return pathlib.Path("/".join(split) + ".py")


def validate_charm(
    charm: str,
    internal_libraries: dict[str, pathlib.Path],
    templates: dict[str, pathlib.Path],
) -> Charm:
    """Validate the charm."""
    charm_build = Charm(charm)

    for library in charm_build.internal_libraries:
        if library not in internal_libraries:
            raise RepositoryError(
                f"Charm {charm} has invalid internal library: {library} not found."
            )
    for template in charm_build.templates:
        if template not in templates:
            raise RepositoryError(f"Charm {charm} has invalid template: {template} not found.")
    return charm_build


def load_internal_libraries() -> dict[str, pathlib.Path]:
    """Load the internal libraries."""
    charms = list((ROOT_DIR / "charms").iterdir())
    libraries = {}
    for charm in charms:
        path = charm / "lib"
        search_path = path / "charms" / charm.name.replace("-", "_")
        libraries.update(
            {
                str(p.relative_to(path))[:-3].replace("/", "."): p
                for p in search_path.glob("**/*.py")
            }
        )
    return libraries


def load_templates() -> dict[str, pathlib.Path]:
    """Load the templates."""
    path = ROOT_DIR / "templates"
    return {str(p.relative_to(path)): p for p in path.glob("**/*")}


def list_charms() -> list[str]:
    """List the available charms."""
    return [p.name for p in (ROOT_DIR / "charms").iterdir() if p.is_dir()]


def copy(src: pathlib.Path, dest: pathlib.Path):
    """Copy the src to dest.

    Only supports files.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(src, dest)


def remove_dir_if_exists(dir: pathlib.Path):
    """Removes the directory `dir` if it exists and it's a directory."""
    try:
        shutil.rmtree(dir)
    except FileNotFoundError:
        # Directory doesn't exist, so skip.
        pass


def stage_charm(
    charm: Charm,
    internal_libraries: dict[str, pathlib.Path],
    templates: dict[str, pathlib.Path],
    dry_run: bool = False,
):
    """Copy the necessary files.

    Will copy internal libraries and templates.
    """
    logger.info(f"Staging charm {charm.path.name}.")
    if not dry_run:
        remove_dir_if_exists(charm.build_path)
        shutil.copytree(charm.path, charm.build_path, dirs_exist_ok=True)
        if charm.metadata.get("charm-libs"):
            CHARMCRAFT.run_command(["fetch-libs"], cwd=charm.build_path)

    for library in charm.internal_libraries:
        path = internal_libraries[library]
        library_path = _library_to_path(library)
        dest = charm.build_path / "lib" / library_path
        if not dest.exists():
            logger.debug(f"Copying {library} to {dest}")
            if dry_run:
                continue
            copy(path, dest)
    for template in charm.templates:
        path = templates[template]
        dest = charm.build_path / "src" / "templates" / template
        if not dest.exists():
            logger.debug(f"Copying {template} to {dest}")
            if dry_run:
                continue
            copy(path, dest)
    logger.info(f"Charm {charm.path.name} staged at {charm.build_path}.")
    UV.run_command(
        [
            "export",
            "--package",
            charm.name,
            "--frozen",
            "--no-hashes",
            "--format=requirements-txt",
            "-o",
            str(charm.build_path / "requirements.txt"),
        ]
    )


def clean_charm(
    charm: Charm,
    dry_run: bool = False,
):
    """Clean charm directory."""
    logger.debug(f"Removing {charm.build_path}")
    if not dry_run:
        shutil.rmtree(charm.build_path, ignore_errors=True)
        charm.charm_path.unlink(missing_ok=True)


def get_source_dirs(charms: [str], include_tests: bool = True) -> [str]:
    """Get all the source directories for the specified charms."""
    charms_dir = ROOT_DIR / "charms"
    files = [
        file
        for charm in charms
        for file in (
            str(charms_dir / charm / "src"),
            str(charms_dir / charm / "tests") if include_tests else "",
        )
        if file
    ]
    return files

def pythonpath(internal_libraries: dict[str, pathlib.Path]) -> str:
    """Get the PYTHONPATH of the project."""
    parent_dirs = set()
    for path in internal_libraries.values():
        parent_dirs.add(path.parents[3])
    return ":".join(str(p) for p in parent_dirs)


def uv_run(args: [str], *popenargs, **kwargs) -> str:
    """Run a command using the uv runner."""
    args = ["run", "--frozen", "--extra", "dev"] + args
    return UV.run_command(args, *popenargs, **kwargs)


###############################################
# Cli Definitions
###############################################
def _add_charm_argument(parser: argparse.ArgumentParser):
    parser.add_argument("charm", type=str, nargs="*", help="The charm to operate on.")


def main_cli():
    """Run the main CLI tool."""
    main_parser = argparse.ArgumentParser(description="Repository utilities.")
    main_parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable verbose logging."
    )
    subparsers = main_parser.add_subparsers(required=True, help="sub-command help")

    stage_parser = subparsers.add_parser("stage", help="Stage charm(s).")
    _add_charm_argument(stage_parser)
    stage_parser.add_argument(
        "--clean",
        action="store_true",
        default=False,
        help="Clean the charm(s) first.",
    )
    stage_parser.add_argument("--dry-run", action="store_true", default=False, help="Dry run.")
    stage_parser.set_defaults(func=stage_cli)

    gen_token_parser = subparsers.add_parser(
        "generate-token", help="Generate Charmhub token to publish charms."
    )
    gen_token_parser.set_defaults(func=gen_token_cli)

    clean_parser = subparsers.add_parser("clean", help="Clean charm(s).")
    _add_charm_argument(clean_parser)
    clean_parser.add_argument("--dry-run", action="store_true", default=False, help="Dry run.")
    clean_parser.set_defaults(func=clean_cli)

    validate_parser = subparsers.add_parser("validate", help="Validate charm(s).")
    _add_charm_argument(validate_parser)
    validate_parser.set_defaults(func=validate_cli)

    pythonpath_parser = subparsers.add_parser("pythonpath", help="Print the pythonpath.")
    pythonpath_parser.set_defaults(func=pythonpath_cli)

    fmt_parser = subparsers.add_parser("fmt", help="Apply formatting standards to code.")
    fmt_parser.set_defaults(func=fmt_cli)

    lint_parser = subparsers.add_parser("lint", help="Check code against coding style standards")
    lint_parser.add_argument(
        "--fix", action="store_true", default=False, help="Try to fix the lint err ors"
    )
    lint_parser.set_defaults(func=lint_cli)

    type_parser = subparsers.add_parser("typecheck", help="Type checking with pyright.")
    _add_charm_argument(type_parser)
    type_parser.set_defaults(func=typecheck_cli)

    unit_test_parser = subparsers.add_parser("unit", help="Run unit tests.")
    _add_charm_argument(unit_test_parser)
    unit_test_parser.set_defaults(func=unit_test_cli)

    build_parser = subparsers.add_parser("build", help="Build all the specified charms.")
    _add_charm_argument(build_parser)
    build_parser.set_defaults(func=build_cli)

    integration_test_parser = subparsers.add_parser("integration", help="Run integration tests.")
    integration_test_parser.add_argument(
        "rest", type=str, nargs="*", help="Arguments forwarded to pytest"
    )
    _add_charm_argument(integration_test_parser)
    integration_test_parser.set_defaults(func=integration_tests_cli)

    fetch_libs_parser = subparsers.add_parser("fetch-libs", help="Fetch external charm libraries.")
    fetch_libs_parser.add_argument(
        "rest", type=str, nargs="*", help="Arguments forwarded to charmcraft fetch-libs"
    )
    _add_charm_argument(fetch_libs_parser)
    fetch_libs_parser.set_defaults(func=fetch_libs_cli)

    args = main_parser.parse_args()
    level = logging.INFO
    if args.verbose:
        level = logging.DEBUG
    logger.setLevel(level)
    context = vars(args)
    context["internal_libraries"] = load_internal_libraries()
    context["templates"] = load_templates()
    context["charms"] = list_charms()
    if "charm" in context:
        charms = context.pop("charm")
        if not charms:
            charms = context["charms"]
        context["charms"] = [
            validate_charm(
                charm,
                context["internal_libraries"],
                context["templates"],
            )
            for charm in charms
        ]
    args.func(**context)


def stage_cli(
    charms: list[Charm],
    internal_libraries: dict[str, pathlib.Path],
    templates: dict[str, pathlib.Path],
    clean: bool = False,
    dry_run: bool = False,
    **kwargs,
):
    """Stage the specified charms into the build directory."""
    for charm in charms:
        logger.info("Preparing the charm %s", charm.path.name)
        if clean:
            clean_charm(charm, dry_run=dry_run)
        stage_charm(
            charm,
            internal_libraries,
            templates,
            dry_run=dry_run,
        )


def gen_token_cli(
    charms: [str],
    **kwargs,
):
    """Generate Charmhub token to publish charms."""
    CHARMCRAFT.run_command(
        ["login", "--export=.charmhub.secret"]
        + [f"--charm={charm}" for charm in charms]
        + [
            "--permission=package-manage-metadata",
            "--permission=package-manage-releases",
            "--permission=package-manage-revisions",
            "--permission=package-view-metadata",
            "--permission=package-view-releases",
            "--permission=package-view-revisions",
            "--ttl=31536000",  # 365 days
        ]
    )


def clean_cli(
    charms: list[Charm],
    internal_libraries: dict[str, pathlib.Path],
    templates: dict[str, pathlib.Path],
    dry_run: bool = False,
    **kwargs,
):
    """Clean all the build artifacts for the specified charms."""
    for charm in charms:
        logger.info("Cleaning the charm %s", charm.path.name)
        clean_charm(charm, dry_run=dry_run)
    if not dry_run:
        try:
            BUILD_PATH.rmdir()
            logger.info(f"Deleted empty build directory {BUILD_PATH}")
        except OSError as e:
            # ENOENT   (2)  - No such file or directory
            # ENOEMPTY (39) - Directory not empty
            if e.errno != 39 and e.errno != 2:
                raise e


def validate_cli(
    charms: list[Charm],
    internal_libraries: dict[str, pathlib.Path],
    templates: dict[str, pathlib.Path],
    **kwargs,
):
    """Validate all the specified charms.

    Currently a no op because this is done in the main_cli.
    """
    for charm in charms:
        logging.info("Charm %s is valid.", charm.path.name)


def pythonpath_cli(internal_libraries: dict[str, pathlib.Path], **kwargs):
    """Print the pythonpath."""

    parent_dirs = set()
    for path in internal_libraries.values():
        parent_dirs.add(path.parents[3])
    print(":".join(str(p) for p in parent_dirs))


def fmt_cli(
    charms: [str],
    **kwargs,
):
    """Apply formatting standards to code."""
    files = get_source_dirs(charms)
    files.append(str(ROOT_DIR / "tests"))
    logging.info(f"Formatting directories {files} with ruff...")
    uv_run(["ruff", "format"] + files, cwd=ROOT_DIR)


def lint_cli(
    charms: [str],
    fix: bool,
    **kwargs,
):
    """Check code against coding style standards."""
    files = get_source_dirs(charms)
    files.append(str(ROOT_DIR / "tests"))
    logging.info("Target directories: {files}")
    if fix:
        logging.info("Trying to automatically fix the lint errors.")
    logging.info("Running codespell...")
    uv_run(["codespell"] + (["-w"] if fix else []) + files, cwd=ROOT_DIR)
    logging.info("Running ruff...")
    uv_run(["ruff", "check"] + (["--fix"] if fix else []) + files, cwd=ROOT_DIR)


def typecheck_cli(
    charms: [Charm],
    internal_libraries: dict[str, pathlib.Path],
    templates: dict[str, pathlib.Path],
    **kwargs,
):
    """Type checking with pyright."""
    for charm in charms:
        logger.info("Staging the charm %s", charm.path.name)
        stage_charm(
            charm,
            internal_libraries,
            templates,
            dry_run=False,
        )
        logger.info("Running pyright...")
        uv_run(
            ["pyright", str(charm.build_path / "src")],
            env={**os.environ, "PYTHONPATH": f"{charm.build_path}/src:{charm.build_path}/lib"},
        )


def unit_test_cli(
    charms: [Charm],
    internal_libraries: dict[str, pathlib.Path],
    templates: dict[str, pathlib.Path],
    **kwargs,
):
    """Run unit tests."""
    UV.run_command(["lock"])
    uv_run(["coverage", "erase"])

    files = []

    for charm in charms:
        logger.info("Staging the charm %s", charm.path.name)
        stage_charm(
            charm,
            internal_libraries,
            templates,
            dry_run=False,
        )
        logger.info("Running unit tests for %s", charm.path.name)
        coverage_file = charm.build_path / ".coverage"
        uv_run(["coverage", "erase"], env={**os.environ, "COVERAGE_FILE": str(coverage_file)})
        uv_run(
            [
                "coverage",
                "run",
                "--source",
                str(charm.build_path / "src"),
                "-m",
                "pytest",
                "-v",
                "--tb",
                "native",
                "-s",
                str(charm.build_path / "tests" / "unit"),
            ],
            env={
                **os.environ,
                "PYTHONPATH": f"{charm.build_path}/src:{charm.build_path}/lib",
                "COVERAGE_FILE": str(coverage_file),
            },
        )
        if coverage_file.is_file():
            files.append(str(coverage_file))

    logger.info("Generating global results...")
    uv_run(["coverage", "combine"] + files)
    uv_run(["coverage", "report"])
    uv_run(["coverage", "xml", "-o", "cover/coverage.xml"])
    logger.info(f"XML report generated at {ROOT_DIR}/cover/coverage.xml")


def build_cli(
    charms: [Charm],
    internal_libraries: dict[str, pathlib.Path],
    templates: dict[str, pathlib.Path],
    **kwargs,
):
    """Build all the specified charms."""
    UV.run_command(["lock"])

    for charm in charms:
        logger.info("Staging the charm %s", charm.name)
        stage_charm(
            charm,
            internal_libraries,
            templates,
            dry_run=False,
        )
        logger.info("Building the charm %s", charm.name)
        subprocess.run(
            "charmcraft -v pack".split(),
            cwd=charm.build_path,
            check=True,
        )

        charm_long_path = (
            charm.build_path
            / glob.glob(f"{charm.path.name}_*.charm", root_dir=charm.build_path)[0]
        )
        logger.info("Moving charm %s to %s", charm_long_path, charm.charm_path)

        charm.charm_path.unlink(missing_ok=True)
        copy(charm_long_path, charm.charm_path)
        charm_long_path.unlink()
        logger.info("Built charm %s", charm.charm_path)


def integration_tests_cli(
    charms: [Charm],
    internal_libraries: dict[str, pathlib.Path],
    templates: dict[str, pathlib.Path],
    rest: [str],
    **kwargs,
):
    """Run integration tests."""
    local_charms = {}
    path = pythonpath(internal_libraries=internal_libraries)

    fetch_libs_cli(charms=charms, internal_libraries=internal_libraries, templates=templates, rest=rest, **kwargs)

    for charm in charms:
        local_charms[f"{charm.name.upper().replace("-", "_")}_DIR"] = charm.build_path

    uv_run(
        ["pytest", "-v", "-s", "--tb", "native", "--log-cli-level=INFO", "./tests/integration"]
        + rest,
        env={"PYTHONPATH": path, **os.environ, **local_charms},
    )


def fetch_libs_cli(
    charms: [Charm],
    internal_libraries: dict[str, pathlib.Path],
    templates: dict[str, pathlib.Path],
    rest: [str],
    **kwargs,
):
    """Fetch external charm libraries."""
    patterns = [f"{lib.replace('.', '/')}.py" for lib in internal_libraries.keys()]

    def ignore_internal_libs(dir: str, items: [str]) -> Iterable[str]:
        ignored = []
        for item in items:
            path = pathlib.Path(dir) / item
            for pattern in patterns:
                if path.match(pattern):
                    ignored.append(item)
                    break
        return ignored

    remove_dir_if_exists(EXTERNAL_LIB_DIR)
    for charm in charms:
        stage_charm(charm, internal_libraries, templates)
        shutil.copytree(
            charm.build_path / "lib",
            EXTERNAL_LIB_DIR,
            dirs_exist_ok=True,
            ignore=ignore_internal_libs,
        )
        for dirpath, dirnames, filenames in EXTERNAL_LIB_DIR.walk(top_down=False):
            for dirname in dirnames:
                try:
                    (dirpath / dirname).rmdir()
                except OSError as e:
                    if e.errno != 39:  # Directory not empty
                        raise e


if __name__ == "__main__":
    main_cli()
