#!/usr/bin/env python3
"""
VM 远程执行模块 —— SSH/SCP 共享基础设施。

通过 SSH 在 Linux VM 上运行 Yosys 综合和 OpenROAD 物理设计。
零 Python 依赖，直接调用 ssh/scp 二进制。

配置方式（优先级从高到低）：
  1. 命令行参数 (--vm-host/--vm-user/--vm-port/--vm-key)
  2. 环境变量: RISCV_VM_HOST, RISCV_VM_USER, RISCV_VM_PORT, RISCV_VM_KEY, RISCV_VM_WORK_DIR
  3. 配置文件: ~/.riscv_vm_config.json

用法（作为独立工具）：
  python vm_runner.py check                  # 测试 SSH 连接
  python vm_runner.py upload <local> <remote> # 上传文件
  python vm_runner.py download <remote> <local> # 下载文件
  python vm_runner.py run "yosys -s synth.ys" # 执行远程命令
"""

import subprocess
import sys
import json
import os
import uuid
import argparse
from pathlib import Path
from datetime import datetime

# ---------------------------------------------------------------------------
# 配置解析
# ---------------------------------------------------------------------------

CONFIG_FILE = Path.home() / ".riscv_vm_config.json"


def get_vm_config(vm_host=None, vm_user=None, vm_port=None, vm_key=None, vm_work_dir=None):
    """解析 VM 连接配置，按优先级：参数 > 环境变量 > 配置文件。

    返回 dict，若缺少必要字段则返回错误信息。
    """
    config = {}

    # 1. 配置文件
    if CONFIG_FILE.exists():
        try:
            file_cfg = json.loads(CONFIG_FILE.read_text())
            config.update(file_cfg)
        except (json.JSONDecodeError, OSError):
            pass

    # 2. 环境变量
    env_map = {
        "host": "RISCV_VM_HOST",
        "user": "RISCV_VM_USER",
        "port": "RISCV_VM_PORT",
        "key_file": "RISCV_VM_KEY",
        "remote_work_dir": "RISCV_VM_WORK_DIR",
    }
    for key, env in env_map.items():
        val = os.environ.get(env)
        if val:
            config[key] = val

    # 3. 命令行参数（最高优先级）
    if vm_host:
        config["host"] = vm_host
    if vm_user:
        config["user"] = vm_user
    if vm_port is not None:
        config["port"] = int(vm_port)
    if vm_key:
        config["key_file"] = vm_key
    if vm_work_dir:
        config["remote_work_dir"] = vm_work_dir

    # 设置默认值
    config.setdefault("port", 22)
    config.setdefault("remote_work_dir", "~/riscv_work")

    # 检查必要字段
    missing = []
    for field in ["host", "user"]:
        if field not in config:
            missing.append(field)

    if missing:
        return {
            "_error": True,
            "_message": (
                f"缺少 VM 连接信息: {', '.join(missing)}。\n"
                f"请设置环境变量:\n"
                f"  export RISCV_VM_HOST=<IP>\n"
                f"  export RISCV_VM_USER=<username>\n"
                f"或创建配置文件 {CONFIG_FILE}:\n"
                f'  {{"host": "<VM_IP>", "user": "<username>", "port": 22}}\n'
            ),
        }

    return config


def _ssh_opts(config):
    """构建 SSH/SCP 通用选项列表。"""
    opts = [
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "ConnectTimeout=10",
        "-o", "ServerAliveInterval=30",
        "-o", "BatchMode=yes",
    ]
    if config.get("key_file"):
        opts += ["-i", config["key_file"]]
    if config.get("port") and config["port"] != 22:
        opts += ["-P", str(config["port"])]
    return opts


def _ssh_dest(config):
    """构建 user@host 字符串。"""
    return f"{config['user']}@{config['host']}"


# ---------------------------------------------------------------------------
# 核心 SSH/SCP 操作
# ---------------------------------------------------------------------------

def check_ssh_connection(config, verbose=True):
    """测试到 VM 的 SSH 连接是否正常。

    返回 (ok: bool, message: str)。
    """
    host = config.get("host", "unknown")
    user = config.get("user", "unknown")

    cmd = ["ssh"] + _ssh_opts(config) + [_ssh_dest(config), "echo ok"]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode == 0 and "ok" in result.stdout:
            if verbose:
                print(f"[VM] SSH 连接成功: {user}@{host}")
            return True, f"已连接到 {user}@{host}"
        else:
            msg = f"SSH 连接失败 (exit={result.returncode}): {result.stderr.strip()}"
            if verbose:
                print(f"[VM] {msg}")
            return False, msg
    except subprocess.TimeoutExpired:
        msg = f"SSH 连接超时: {user}@{host}"
        if verbose:
            print(f"[VM] {msg}")
        return False, msg
    except FileNotFoundError:
        msg = "未找到 ssh 命令。请安装 OpenSSH 客户端。"
        if verbose:
            print(f"[VM] {msg}")
        return False, msg
    except OSError as e:
        msg = f"SSH 错误: {e}"
        if verbose:
            print(f"[VM] {msg}")
        return False, msg


def ssh_run(config, command, timeout=300, verbose=True):
    """在 VM 上执行命令。

    返回 (returncode: int, stdout: str, stderr: str)。
    """
    cmd = ["ssh"] + _ssh_opts(config) + [_ssh_dest(config), command]
    if verbose:
        print(f"[VM] 执行: {command[:100]}{'...' if len(command) > 100 else ''}")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return -1, "", f"命令超时 ({timeout}s): {command[:80]}"
    except OSError as e:
        return -1, "", f"SSH 错误: {e}"


def scp_upload(config, local_path, remote_path, verbose=True):
    """上传文件/目录到 VM。

    返回 (ok: bool, message: str)。
    """
    local = Path(local_path)
    if not local.exists():
        return False, f"本地文件不存在: {local_path}"

    dest = f"{_ssh_dest(config)}:{remote_path}"
    scp_opts = _ssh_opts(config)
    # -r for directories
    cmd = ["scp", "-r"] + scp_opts + [str(local).replace('\\', '/'), dest]

    if verbose:
        print(f"[VM] 上传: {local.name} → {dest}")

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode == 0:
            return True, f"已上传 {local_path}"
        else:
            return False, f"上传失败: {result.stderr.strip()}"
    except subprocess.TimeoutExpired:
        return False, "上传超时"
    except OSError as e:
        return False, f"SCP 错误: {e}"


def scp_download(config, remote_path, local_path, verbose=True):
    """从 VM 下载文件/目录。

    返回 (ok: bool, message: str)。
    """
    src = f"{_ssh_dest(config)}:{remote_path}"
    local = str(Path(local_path)).replace('\\', '/')
    scp_opts = _ssh_opts(config)
    cmd = ["scp", "-r"] + scp_opts + [src, local]

    if verbose:
        print(f"[VM] 下载: {src} → {local}")

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode == 0:
            return True, f"已下载到 {local_path}"
        else:
            return False, f"下载失败: {result.stderr.strip()}"
    except subprocess.TimeoutExpired:
        return False, "下载超时"
    except OSError as e:
        return False, f"SCP 错误: {e}"


# ---------------------------------------------------------------------------
# 远程工作目录管理
# ---------------------------------------------------------------------------

def create_remote_temp_dir(config, verbose=True):
    """在 VM 上创建临时工作目录。返回远程路径或 None。"""
    work_dir = config.get("remote_work_dir", "~/riscv_work")
    session_id = uuid.uuid4().hex[:8]
    remote_dir = f"{work_dir}/job_{session_id}"

    ret, stdout, stderr = ssh_run(
        config,
        f"mkdir -p {remote_dir} && echo {remote_dir}",
        timeout=30,
        verbose=verbose,
    )
    if ret == 0 and stdout.strip():
        actual_dir = stdout.strip()
        if verbose:
            print(f"[VM] 远程工作目录: {actual_dir}")
        return actual_dir
    return None


def cleanup_remote_temp_dir(config, remote_dir, verbose=True):
    """删除 VM 上的临时工作目录。"""
    if not remote_dir:
        return
    if verbose:
        print(f"[VM] 清理远程目录: {remote_dir}")
    ssh_run(config, f"rm -rf {remote_dir}", timeout=30, verbose=False)


# ---------------------------------------------------------------------------
# 高级工作流
# ---------------------------------------------------------------------------

def remote_yosys_synth(config, rtl_files, synth_script, output_dir, verbose=False):
    """在 VM 上执行完整的 Yosys 综合流程。

    参数:
        config: VM 连接配置
        rtl_files: 本地 RTL 文件列表
        synth_script: Yosys TCL 脚本内容 (str)
        output_dir: 本地输出目录
        verbose: 详细输出

    流程: 上传 RTL + 脚本 → VM 运行 Yosys → 下载结果 → 清理

    返回 (returncode, local_output_dir)。
    """
    local_output = Path(output_dir)
    local_output.mkdir(parents=True, exist_ok=True)

    # 1. 创建远程工作目录
    remote_dir = create_remote_temp_dir(config)
    if not remote_dir:
        return -1, output_dir

    try:
        remote_src = f"{remote_dir}/src"
        ret, _, _ = ssh_run(config, f"mkdir -p {remote_src}", timeout=10, verbose=False)

        # 2. 上传 RTL 文件
        for f in rtl_files:
            ok, msg = scp_upload(config, f, f"{remote_src}/")
            if not ok:
                print(f"[错误] {msg}")
                return -1, output_dir

        # 3. 生成并上传综合脚本（使用远程路径）
        remote_synth_script = synth_script
        # 替换脚本中的本地路径为远程路径
        for f in rtl_files:
            local_p = Path(f)
            remote_p = f"{remote_src}/{local_p.name}"
            remote_synth_script = remote_synth_script.replace(
                str(local_p.resolve()).replace('\\', '/'), remote_p
            )
            remote_synth_script = remote_synth_script.replace(
                str(local_p), remote_p
            )

        local_script = local_output / "synth.ys"
        local_script.write_text(remote_synth_script)
        ok, _ = scp_upload(config, str(local_script), f"{remote_dir}/synth.ys")
        if not ok:
            print("[错误] 无法上传综合脚本")
            return -1, output_dir

        # 4. 运行 Yosys
        print(f"\n[VM] 开始远程综合...")
        ret, stdout, stderr = ssh_run(
            config,
            f"cd {remote_dir} && yosys -s synth.ys -q",
            timeout=600,
        )

        # 保存日志
        log_path = local_output / "yosys_log.txt"
        log_path.write_text(stdout + "\n" + stderr)

        if ret != 0:
            print(f"[VM] Yosys 返回非零退出码 {ret}")
            print(f"  完整日志: {log_path}")

        # 5. 下载结果
        print(f"[VM] 下载综合结果...")
        remote_output = f"{remote_dir}/output"
        scp_download(config, f"{remote_output}/", str(local_output) + "/")

        return ret, output_dir

    finally:
        # 6. 清理远程目录
        cleanup_remote_temp_dir(config, remote_dir)


def remote_openroad_flow(config, netlist_path, pdk_uploads, tcl_script,
                         output_dir, verbose=False):
    """在 VM 上执行完整的 OpenROAD 物理设计流程。

    参数:
        config: VM 连接配置
        netlist_path: 本地综合网表路径
        pdk_uploads: [(label, local_path), ...] PDK 文件列表
        tcl_script: OpenROAD TCL 脚本内容 (str)
        output_dir: 本地输出目录
        verbose: 详细输出

    流程: 上传网表 + PDK + TCL → VM 运行 OpenROAD → 下载 DEF/GDS/报告 → 清理

    返回 (returncode, local_output_dir)。
    """
    local_output = Path(output_dir)
    local_output.mkdir(parents=True, exist_ok=True)

    # 1. 创建远程工作目录
    remote_dir = create_remote_temp_dir(config)
    if not remote_dir:
        return -1, output_dir

    try:
        # 2. 上传综合网表
        ok, msg = scp_upload(config, netlist_path, f"{remote_dir}/")
        if not ok:
            print(f"[错误] {msg}")
            return -1, output_dir

        # 3. 上传 PDK 文件
        remote_pdk = f"{remote_dir}/pdk"
        ssh_run(config, f"mkdir -p {remote_pdk}", timeout=10, verbose=False)

        for label, local_p in pdk_uploads:
            ok, _ = scp_upload(config, str(local_p), f"{remote_pdk}/")
            if not ok:
                print(f"[警告] 上传{label}失败: {local_p}")

        # 4. 上传 OpenROAD TCL 脚本
        local_tcl = local_output / "run_pd.tcl"
        local_tcl.write_text(tcl_script)
        ok, _ = scp_upload(config, str(local_tcl), f"{remote_dir}/run_pd.tcl")
        if not ok:
            print("[错误] 无法上传 OpenROAD TCL 脚本")
            return -1, output_dir

        # 5. 运行 OpenROAD
        print(f"\n[VM] 开始远程物理设计 (预计 10-30 分钟)...")
        ret, stdout, stderr = ssh_run(
            config,
            f"cd {remote_dir} && openroad -no_init -exit run_pd.tcl 2>&1",
            timeout=3600,
        )

        # 保存日志
        log_path = local_output / "openroad_log.txt"
        log_path.write_text(stdout + "\n" + stderr)

        if ret != 0:
            print(f"[VM] OpenROAD 返回非零退出码 {ret}")
            print(f"  完整日志: {log_path}")

        # 6. 下载结果
        print(f"[VM] 下载物理设计结果...")
        scp_download(config, f"{remote_dir}/outputs/", str(local_output) + "/")
        scp_download(config, f"{remote_dir}/reports/", str(local_output) + "/")

        return ret, output_dir

    finally:
        # 7. 清理
        cleanup_remote_temp_dir(config, remote_dir)


# ---------------------------------------------------------------------------
# CLI 接口
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="VM 远程执行工具 —— SSH/SCP 连接管理"
    )
    parser.add_argument("--vm-host", default=None, help="VM 主机名/IP")
    parser.add_argument("--vm-user", default=None, help="VM SSH 用户名")
    parser.add_argument("--vm-port", type=int, default=None, help="VM SSH 端口 (默认 22)")
    parser.add_argument("--vm-key", default=None, help="SSH 私钥路径")

    sub = parser.add_subparsers(dest="action", help="操作")

    # check
    sub.add_parser("check", help="测试 SSH 连接")

    # upload
    p = sub.add_parser("upload", help="上传文件到 VM")
    p.add_argument("local", help="本地文件路径")
    p.add_argument("remote", help="远程路径")

    # download
    p = sub.add_parser("download", help="从 VM 下载文件")
    p.add_argument("remote", help="远程路径")
    p.add_argument("local", help="本地路径")

    # run
    p = sub.add_parser("run", help="在 VM 上执行命令")
    p.add_argument("command", help="要执行的命令")
    p.add_argument("--timeout", type=int, default=300, help="超时秒数 (默认 300)")

    # test  (等同于 check + 打印详细信息)
    sub.add_parser("test", help="详细测试连接")

    args = parser.parse_args()

    if not args.action:
        parser.print_help()
        sys.exit(1)

    config = get_vm_config(
        vm_host=args.vm_host,
        vm_user=args.vm_user,
        vm_port=args.vm_port,
        vm_key=args.vm_key,
    )

    if config.get("_error"):
        print(config["_message"])
        sys.exit(1)

    if args.action in ("check", "test"):
        verbose = args.action == "test"
        ok, msg = check_ssh_connection(config, verbose=verbose)
        if verbose and ok:
            # 额外信息
            ret, stdout, _ = ssh_run(config, "uname -a && which yosys && which openroad", verbose=False)
            print(f"[VM] 系统信息:\n{stdout}")
        sys.exit(0 if ok else 1)

    elif args.action == "upload":
        ok, msg = scp_upload(config, args.local, args.remote)
        print(msg)
        sys.exit(0 if ok else 1)

    elif args.action == "download":
        ok, msg = scp_download(config, args.remote, args.local)
        print(msg)
        sys.exit(0 if ok else 1)

    elif args.action == "run":
        ret, stdout, stderr = ssh_run(config, args.command, timeout=args.timeout)
        if stdout:
            print(stdout)
        if stderr:
            print(stderr, file=sys.stderr)
        sys.exit(ret)


if __name__ == "__main__":
    main()
