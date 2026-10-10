"""本地工具使用的受限文件路径；目录由当前任务显式传入。"""
from pathlib import Path


def resolve_file(name, workspace, output, write=False):
    root, output = Path(workspace).resolve(), Path(output).resolve()
    if not output.is_relative_to(root / "resumes"):
        raise ValueError("输出目录无效")
    path = Path(name)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("只能使用工作目录内的相对路径")
    if name == "项目经历库.md":
        target = root / name
        boundary = root
    elif path.parts and path.parts[0] == "模板" and not write:
        target = root / path
        boundary = root / "模板"
    else:
        target = output / path
        boundary = output
        if write and path.suffix != ".tex":
            raise ValueError("只允许更新项目经历库.md 或写入本轮 .tex 文件")
    resolved = target.resolve()
    if not resolved.is_relative_to(boundary) or target.is_symlink():
        raise ValueError("文件路径越界或使用了符号链接")
    if write and path.parts and path.parts[0] == "模板":
        raise ValueError("不能修改模板")
    return resolved
