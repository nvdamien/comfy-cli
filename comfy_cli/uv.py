from importlib import metadata
import os
from pathlib import Path
import shutil
import subprocess
import sys
from textwrap import dedent
from typing import Any, Optional, Union

from comfy_cli.constants import GPU_OPTION

PathLike = Union[os.PathLike[str], str]

def _run(cmd: list[str], cwd: PathLike) -> subprocess.CompletedProcess[Any]:
    return subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
    )

def _check_call(cmd: list[str], cwd: Optional[PathLike] = None):
    """uses check_call to run pip, as reccomended by the pip maintainers.
    see https://pip.pypa.io/en/stable/user_guide/#using-pip-from-your-program"""

    subprocess.check_call(cmd, cwd=cwd)

class DependencyCompiler:
    rocmPytorchUrl = "https://download.pytorch.org/whl/rocm6.0"
    nvidiaPytorchUrl = "https://download.pytorch.org/whl/cu121"

    overrideGpu = dedent("""
        # ensure usage of {gpu} version of pytorch
        --extra-index-url {gpuUrl}
        torch
        torchsde
        torchvision
    """).strip()

    reqNames = {
        "requirements.txt",
        "pyproject.toml",
        "setup.cfg",
        "setup.py",
    }

    @staticmethod
    def FindReqFiles(*ders: PathLike) -> list[Path]:
        return [file
            for der in ders
            for file in Path(der).absolute().iterdir()
            if file.name in DependencyCompiler.reqNames
        ]

    @staticmethod
    def InstallBuildDeps():
        """Use pip to install bare minimum requirements for uv to do its thing
        """
        if shutil.which("uv") is None:
            _check_call(cmd=["python", "-m", "pip", "install", "-U", "pip"])
            _check_call(cmd=["python", "-m", "pip", "install", "uv"])

    @staticmethod
    def Compile(
        cwd: PathLike,
        reqFiles: list[PathLike],
        override: Optional[PathLike] = None,
        out: Optional[PathLike] = None,
        index_strategy: Optional[str] = "unsafe-best-match",
    ) -> subprocess.CompletedProcess[Any]:
        cmd = [
            sys.executable,
            "-m",
            "uv",
            "pip",
            "compile",
        ]

        for reqFile in reqFiles:
            cmd.append(str(reqFile))

        # ensures that eg tqdm is latest version, even though an old tqdm is on the amd url
        # see https://github.com/astral-sh/uv/blob/main/PIP_COMPATIBILITY.md#packages-that-exist-on-multiple-indexes and https://github.com/astral-sh/uv/issues/171
        if index_strategy is not None:
            cmd.extend([
                "--index-strategy",
                "unsafe-best-match",
            ])

        if override is not None:
            cmd.extend([
                "--override",
                str(override),
            ])

        if out is not None:
            cmd.extend([
                "-o",
                str(out),
            ])

        return _run(cmd, cwd)

    @staticmethod
    def Install(
        cwd: PathLike,
        reqFile: list[PathLike],
        override: Optional[PathLike] = None,
        extraUrl: Optional[str] = None,
        index_strategy: Optional[str] = "unsafe-best-match",
        dry: bool = False
    ) -> subprocess.CompletedProcess[Any]:
        cmd = [
            sys.executable,
            "-m",
            "uv",
            "pip",
            "install",
            "-r",
            str(reqFile),
        ]

        if index_strategy is not None:
            cmd.extend([
                "--index-strategy",
                "unsafe-best-match",
            ])

        if extraUrl is not None:
            cmd.extend([
                "--extra-index-url",
                extraUrl,
            ])

        if override is not None:
            cmd.extend([
                "--override",
                str(override),
            ])

        if dry:
            cmd.append("--dry-run")

        return _check_call(cmd, cwd)

    @staticmethod
    def Sync(
        cwd: PathLike,
        reqFile: list[PathLike],
        extraUrl: Optional[str] = None,
        index_strategy: Optional[str] = "unsafe-best-match",
        dry: bool = False
    ) -> subprocess.CompletedProcess[Any]:
        cmd = [
            sys.executable,
            "-m",
            "uv",
            "pip",
            "sync",
            str(reqFile),
        ]

        if index_strategy is not None:
            cmd.extend([
                "--index-strategy",
                "unsafe-best-match",
            ])

        if extraUrl is not None:
            cmd.extend([
                "--extra-index-url",
                extraUrl,
            ])

        if dry:
            cmd.append("--dry-run")

        return _check_call(cmd, cwd)

    @staticmethod
    def ResolveGpu(gpu: Union[str, None]):
        if gpu is None:
            try:
                tver = metadata.version("torch")
                if "+cu" in tver:
                    return GPU_OPTION.NVIDIA
                elif "+rocm" in tver:
                    return GPU_OPTION.AMD
                else:
                    return None
            except metadata.PackageNotFoundError:
                return None
        else:
            return gpu

    def __init__(
        self,
        cwd: PathLike = ".",
        reqFilesCore: Optional[list[PathLike]] = None,
        reqFilesExt: Optional[list[PathLike]] = None,
        gpu: Optional[str] = None,
        outName: str = "requirements.compiled",
    ):
        self.cwd = Path(cwd)
        self.reqFiles = [Path(reqFile) for reqFile in reqFilesExt] if reqFilesExt is not None else None
        self.gpu = DependencyCompiler.ResolveGpu(gpu)

        self.gpuUrl = DependencyCompiler.nvidiaPytorchUrl if self.gpu == GPU_OPTION.NVIDIA else DependencyCompiler.rocmPytorchUrl if self.gpu == GPU_OPTION.AMD else None
        self.out = self.cwd / outName
        self.override = self.cwd / "override.txt"

        self.reqFilesCore = reqFilesCore if reqFilesCore is not None else self.findCoreReqs()
        self.reqFilesExt = reqFilesExt if reqFilesExt is not None else self.findExtReqs()

    def findCoreReqs(self):
        return DependencyCompiler.FindReqFiles(self.cwd)

    def findExtReqs(self):
        extDirs = [d for d in (self.cwd / "custom_nodes").iterdir() if d.is_dir() and d.name != "__pycache__"]
        return DependencyCompiler.FindReqFiles(*extDirs)

    def makeOverride(self):
        #clean up
        self.override.unlink(missing_ok=True)

        with open(self.override, "w") as f:
            if self.gpu is not None:
                f.write(DependencyCompiler.overrideGpu.format(gpu=self.gpu, gpuUrl=self.gpuUrl))
                f.write("\n\n")

        coreOverride = DependencyCompiler.Compile(
            cwd=self.cwd,
            reqFiles=self.reqFilesCore,
            override=self.override
        )

        with open(self.override, "a") as f:
            f.write("# ensure that core comfyui deps take precedence over any 3rd party extension deps\n")
            for line in coreOverride.stdout:
                f.write(line)
            f.write("\n")

    def compileCorePlusExt(self):
        #clean up
        self.out.unlink(missing_ok=True)

        DependencyCompiler.Compile(
            cwd=self.cwd,
            reqFiles=(self.reqFilesCore + self.reqFilesExt),
            override=self.override,
            out=self.out,
        )

    def installCorePlusExt(self):
        DependencyCompiler.Install(
            cwd=self.cwd,
            reqFile=self.out,
            override=self.override,
            extraUrl=self.gpuUrl,
        )

    def syncCorePlusExt(self):
        DependencyCompiler.Sync(
            cwd=self.cwd,
            reqFile=self.out,
            extraUrl=self.gpuUrl,
        )

    def handleOpencv(self):
        """as per the opencv docs, you should only have exactly one opencv package.
        headless is more suitable for comfy than the gui version, so remove gui if
        headless is present. TODO: add support for contrib pkgs. see: https://github.com/opencv/opencv-python"""

        with open(self.out, "r") as f:
            lines = f.readlines()

        guiFound, headlessFound = False, False
        for line in lines:
            if "opencv-python==" in line:
                guiFound = True
            elif "opencv-python-headless==" in line:
                headlessFound = True

        if headlessFound and guiFound:
            with open(self.out, "w") as f:
                for line in lines:
                    if "opencv-python==" not in line:
                        f.write(line)

    def installComfyDeps(self):
        DependencyCompiler.InstallBuildDeps()

        self.makeOverride()
        self.compileCorePlusExt()
        self.handleOpencv()

        self.installCorePlusExt()
