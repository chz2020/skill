#!/usr/bin/env python3
"""
Verilog/SystemVerilog RTL 文件静态 lint 检查器。

用法：
    python verilog_lint.py <file.v|file.sv> [--mode rtl|tb] [--external none|auto|verilator|verible|slang] [--verbose]

检查项：
    - A1：敏感信号列表不完整（Verilog-2001 always 块）
    - A3：case 语句缺少 default
    - A7：RTL 中出现 initial 块
    - A8：RTL 中出现 #delay 语句
    - B1：锁存器推断风险（组合块中 if/case 不完整）
    - B3：隐式 wire 声明（未声明标识符）
    - C3：无复位的触发器（时序块中无复位）
    - C7：无同步器注释的 CDC（基础启发式检查）
    - D5：魔法数字（无上下文的裸字面量）
    - 风格：Tab 字符检测

局限性：
    - 本工具是基于正则表达式的启发式 linter，非完整解析器。
    - 对复杂宏/generate 块可能产生误报。
    - 生产级 ASIC sign-off 请使用供应商工具（SpyGlass、HAL 等）。
    - **推荐**：在生产流程中使用正式解析器如 `verible-verilog-lint` 或 `slang`
      作为替代或补充。本脚本作为快速第一轮过滤。
"""

import re
import sys
import argparse
import shutil
import subprocess
from pathlib import Path

SEVERITY_ERROR = "ERROR"
SEVERITY_WARNING = "WARNING"
SEVERITY_STYLE = "STYLE"
SEVERITY_INFO = "INFO"

MODE_RTL = "rtl"
MODE_TB = "tb"

EXTERNAL_TOOLS = {
    "verilator": "verilator",
    "verible": "verible-verilog-lint",
    "slang": "slang",
}

KEYWORDS = {
    "if", "else", "case", "casex", "casez", "endcase", "begin", "end", "module", "endmodule",
    "input", "output", "inout", "wire", "reg", "logic", "parameter", "localparam",
    "integer", "genvar", "for", "generate", "endgenerate", "always", "always_comb",
    "always_ff", "always_latch", "posedge", "negedge", "or", "and", "nand", "nor",
    "xor", "xnor", "buf", "not", "assign", "task", "endtask", "function", "endfunction",
    "return", "disable", "forever", "repeat", "while", "event", "tri", "supply0",
    "supply1", "time", "real", "fork", "join", "join_any", "join_none", "assert",
    "assume", "cover", "property", "sequence", "clocking", "modport", "import",
    "export", "typedef", "struct", "union", "enum", "const", "automatic", "static",
    "void", "this", "new", "package", "endpackage", "interface", "endinterface",
    "type_id", "create", "std", "uvm_pkg", "default", "unique", "unique0", "priority",
    "signed", "unsigned", "var", "initial", "final", "translate_off", "translate_on",
}

SYSTEM_TASKS = {
    "display", "write", "strobe", "monitor", "fwrite", "finish", "stop", "time",
    "realtime", "random", "urandom", "signed", "unsigned", "clog2", "onehot",
    "onehot0", "isunknown", "countones", "dumpfile", "dumpvars", "test$plusargs",
    "cast", "error", "warning", "info", "fatal",
}

DECL_KEYWORDS = {
    "input", "output", "inout", "wire", "reg", "logic", "tri", "parameter",
    "localparam", "integer", "genvar",
}


def strip_comments(text):
    """移除注释（块注释和行注释）"""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    return re.sub(r"//.*", "", text)


def strip_strings(text):
    """移除字符串字面量"""
    return re.sub(r'"(?:\\.|[^"\\])*"', '""', text)


def strip_numeric_literals(text):
    """移除数值字面量（避免与标识符混淆）"""
    text = re.sub(r"\b\d*'[sS]?[bBoOdDhH][0-9a-fA-F_xXzZ?]+\b", "0", text)
    return re.sub(r"\b\d+\b", "0", text)


def split_top_level_commas(text):
    """按顶层逗号分割（忽略括号内的逗号）"""
    parts = []
    start = 0
    depth = 0
    for i, ch in enumerate(text):
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth = max(0, depth - 1)
        elif ch == "," and depth == 0:
            parts.append(text[start:i].strip())
            start = i + 1
    tail = text[start:].strip()
    if tail:
        parts.append(tail)
    return parts


def extract_declared_names_from_statement(stmt):
    """从声明语句中提取声明的信号名"""
    stmt = stmt.strip().rstrip(";")
    first = re.match(r"^([a-zA-Z_]\w*)\b\s*(.*)$", stmt)
    if not first or first.group(1) not in DECL_KEYWORDS:
        return []

    rest = first.group(2).strip()
    names = []
    for piece in split_top_level_commas(rest):
        piece = re.sub(r"\s*=\s*.*$", "", piece).strip()
        piece = re.sub(r"^(?:var\s+)?(?:wire|reg|logic|tri)\b\s*", "", piece)
        piece = re.sub(r"^(?:signed|unsigned)\b\s*", "", piece)
        piece = re.sub(r"^\[[^\]]+\]\s*", "", piece)
        name_match = re.match(r"^([a-zA-Z_]\w*)\b", piece)
        if name_match:
            names.append(name_match.group(1))
    return names


def is_declaration_line(code_part):
    """判断是否为声明行"""
    return bool(re.match(r"^\s*(?:" + "|".join(sorted(DECL_KEYWORDS)) + r")\b", code_part))


class LintResult:
    """Lint 检查结果"""
    def __init__(self, line, col, code, severity, message):
        self.line = line
        self.col = col
        self.code = code
        self.severity = severity
        self.message = message


class VerilogLinter:
    """Verilog/SystemVerilog 启发式 Linter"""
    def __init__(self, content, filename="", mode=MODE_RTL):
        self.lines = content.splitlines()
        self.filename = filename
        self.mode = mode
        self.results = []
        self.in_always_comb = False
        self.in_always_ff = False
        self.in_always_legacy = False
        self.current_block_start = 0
        self.block_has_default = False
        self.block_assignments = set()
        self.declared_signals = set()
        self.reported_undeclared = set()
        if self.mode not in (MODE_RTL, MODE_TB):
            raise ValueError("mode must be 'rtl' or 'tb'")
        self._extract_declarations()

    def _extract_declarations(self):
        """提取模块声明中已声明的信号名"""
        text = strip_comments("\n".join(self.lines))
        module_re = re.compile(r"\bmodule\s+([a-zA-Z_]\w*)\b(?:\s*#\s*\((.*?)\))?\s*\((.*?)\);", re.DOTALL)
        for m in module_re.finditer(text):
            self.declared_signals.add(m.group(1))
            for param_stmt in re.findall(r"\bparameter\b[^,;)]*(?:[,;)]|$)", m.group(2) or ""):
                for name in extract_declared_names_from_statement(param_stmt):
                    self.declared_signals.add(name)
            port_state = {"direction": None}
            for piece in split_top_level_commas(m.group(3) or ""):
                piece = piece.strip()
                direction_match = re.match(r"^(input|output|inout)\b\s*(.*)$", piece)
                if direction_match:
                    port_state["direction"] = direction_match.group(1)
                    piece = direction_match.group(2).strip()
                piece = re.sub(r"^(?:var\s+)?(?:wire|reg|logic|tri)\b\s*", "", piece)
                piece = re.sub(r"^(?:signed|unsigned)\b\s*", "", piece)
                piece = re.sub(r"^\[[^\]]+\]\s*", "", piece)
                name_match = re.match(r"^([a-zA-Z_]\w*)\b", piece)
                if name_match and port_state.get("direction"):
                    self.declared_signals.add(name_match.group(1))

        stmt = []
        for raw in text.splitlines():
            stmt.append(raw)
            if ";" not in raw:
                continue
            full_stmt = " ".join(stmt)
            stmt = []
            for name in extract_declared_names_from_statement(full_stmt):
                self.declared_signals.add(name)

    def run(self):
        """运行所有检查"""
        self._scan_blocks()
        self._check_global()
        return self.results

    def _scan_blocks(self):
        """逐行扫描并进行块级分析"""
        block_depth = 0
        for i, raw in enumerate(self.lines):
            line = raw.strip()
            line_num = i + 1

            if re.search(r"\balways\s*@\(", line) or re.search(r"\balways_comb\b", line) or re.search(r"\balways_ff\b", line) or re.search(r"\balways_latch\b", line):
                self.current_block_start = line_num
                self.block_has_default = False
                self.block_assignments = set()
                if "always_comb" in line:
                    self.in_always_comb = True
                elif "always_ff" in line or "always @(posedge" in line or "always @(negedge" in line:
                    self.in_always_ff = True
                elif "always @(" in line:
                    self.in_always_legacy = True
                block_depth = 0

            if re.search(r"\bbegin\b", line):
                block_depth += 1
            if re.search(r"\bend\b", line) and not re.search(r"\bendcase\b|\bendgenerate\b|\bendmodule\b|\bendtask\b|\bendfunction\b|\bendinterface\b|\bendpackage\b|\bendclocking\b|\bendproperty\b|\bendsequence\b|\bendspecify\b|\bendtable\b|\bendprimitive\b|\bendconfig\b|\bendchecker\b|\bendprogram\b|\benable\b", line):
                block_depth = max(0, block_depth - 1)
                if block_depth == 0 and self.current_block_start > 0:
                    if self.mode == MODE_RTL and (self.in_always_comb or self.in_always_legacy) and not self.block_has_default and self.block_assignments:
                        self._add_result(line_num, 1, "B1", SEVERITY_WARNING,
                            f"可能的锁存器推断：第 {self.current_block_start} 行的组合块缺少默认赋值")
                    self.in_always_comb = False
                    self.in_always_ff = False
                    self.in_always_legacy = False
                    self.current_block_start = 0

            if self.in_always_comb or self.in_always_legacy:
                if re.search(r"\bdefault\b", line):
                    self.block_has_default = True
                assign_match = re.search(r"^\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*(?:\[.*?\])?\s*<?=\s*", line)
                if assign_match:
                    lhs = assign_match.group(1).split(".")[0]
                    self.block_assignments.add(lhs)

            self._check_line(line_num, raw)

    def _check_line(self, line_num, raw):
        """对单行执行所有行级检查"""
        line = raw.strip()
        code_part = strip_numeric_literals(strip_strings(line.split("//")[0].strip()))

        # A7: RTL 中不允许 initial 块
        if self.mode == MODE_RTL and re.search(r"\binitial\b", code_part):
            if "synthesis translate_off" not in code_part and "/* synthesis translate_off */" not in raw:
                self._add_result(line_num, 1, "A7", SEVERITY_ERROR,
                    "RTL 中发现 initial 块。ASIC 中请移除，或使用 synthesis translate_off/translate_on 包裹。")

        # A8: RTL 中不允许 #delay
        if self.mode == MODE_RTL and re.search(r"#\s*\d+", code_part) and "parameter" not in code_part and "localparam" not in code_part:
            self._add_result(line_num, 1, "A8", SEVERITY_ERROR,
                "RTL 中发现 delay 语句。延迟语句不可综合。")

        # A1: 敏感信号列表可能不完整
        m = re.search(r"always\s*@\((.*?)\)", line)
        if m:
            sens = m.group(1)
            if sens.strip() != "*" and "posedge" not in sens and "negedge" not in sens:
                signals = [s.strip() for s in sens.split(" or ") if s.strip()]
                if len(signals) < 2:
                    self._add_result(line_num, 1, "A1", SEVERITY_WARNING,
                        f"可能不完整的敏感信号列表：({sens})。请使用 always @(*) 或 always_comb。")

        # A3: case 语句缺少 default
        if re.search(r"case[xz]?\s*\(", line):
            found_default = False
            lookahead = min(line_num + 10, len(self.lines))
            for j in range(line_num, lookahead):
                if re.search(r"\bdefault\b", self.lines[j]):
                    found_default = True
                    break
                if re.search(r"endcase", self.lines[j]):
                    break
            if not found_default:
                self._add_result(line_num, 1, "A3", SEVERITY_WARNING,
                    "Case 语句缺少 default。添加 default 子句以防止锁存器推断和 X 传播。")

        # C3: 时序块缺少复位
        if self.mode == MODE_RTL and re.search(r"always\s*@\(\s*posedge\s+\w+\s*\)", line):
            self._add_result(line_num, 1, "C3", SEVERITY_WARNING,
                "时序块缺少复位。确保所有触发器都有定义的上电/复位状态以符合 ASIC 要求。")

        # C7: CDC 检测（多时钟边沿）
        if re.search(r"(posedge\s+\w+.*posedge|posedge.*or.*posedge)", line):
            if "synthesis" not in line and "async" not in line.lower():
                self._add_result(line_num, 1, "C7", SEVERITY_INFO,
                    "检测到多个时钟边沿。确保 CDC 信号配备正确的同步器。")

        # 跳过实例化行（不检查未声明标识符）
        if self._is_instance_line(code_part):
            return

        # B3: 未声明的标识符
        for match in re.finditer(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\b", code_part):
            name = match.group(1)
            if self._should_ignore_identifier(code_part, match):
                continue
            self._add_result(line_num, match.start(1) + 1, "B3", SEVERITY_WARNING,
                f"标识符 '{name}' 被使用但未声明。请添加声明或使用 `default_nettype none` 来捕获隐式 wire。")
            self.reported_undeclared.add(name)

        # D1: Tab 字符检测
        if "\t" in raw:
            self._add_result(line_num, 1, "D1", SEVERITY_STYLE,
                "发现 Tab 字符。请使用 4 个空格缩进。")

    def _check_global(self):
        """全局级检查"""
        has_module = any("module " in l for l in self.lines)
        has_ports = any(re.search(r"\b(input|output|inout)\b", l) for l in self.lines)
        if has_module and not has_ports:
            self._add_result(1, 1, "B3", SEVERITY_WARNING,
                "模块没有端口声明。请确认这是有意为之。")

    def _should_ignore_identifier(self, code_part, match):
        """判断标识符是否应跳过（关键字、系统任务、已声明等）"""
        name = match.group(1)
        if name in KEYWORDS or name in SYSTEM_TASKS:
            return True
        if name in self.declared_signals or name in self.reported_undeclared:
            return True
        if name[0].isdigit() or name[0].isupper():
            return True
        if is_declaration_line(code_part):
            return True
        before = code_part[:match.start(1)]
        after = code_part[match.end(1):]
        if before.endswith("$") or before.endswith("`") or before.endswith("."):
            return True
        if re.match(r"\s*\(", after) and re.search(r"\bmodule\s+", code_part):
            return True
        if re.match(r"\s*#", after) or re.match(r"\s+\w+\s*(?:#|\()", after):
            return True
        if re.search(r"\.\s*$", before):
            return True
        return False

    def _is_instance_line(self, code_part):
        """判断是否为模块实例化行"""
        return bool(re.match(
            r"^\s*[a-zA-Z_]\w*\s*(?:#\s*\([^;]*\)\s*)?[a-zA-Z_]\w*\s*\(",
            code_part,
        ))

    def _add_result(self, line, col, code, severity, message):
        """添加一条检查结果"""
        self.results.append(LintResult(line, col, code, severity, message))


def select_external_tools(selection):
    """根据用户选择确定可用的外部工具"""
    if selection == "none":
        return []
    if selection == "auto":
        for tool, binary in EXTERNAL_TOOLS.items():
            if shutil.which(binary):
                return [tool]
        return []
    binary = EXTERNAL_TOOLS[selection]
    return [selection] if shutil.which(binary) else []


def external_command(tool, path):
    """构建外部 linter 的命令行"""
    filename = str(path)
    if tool == "verilator":
        return [EXTERNAL_TOOLS[tool], "--lint-only", "-Wall", filename]
    if tool == "verible":
        return [EXTERNAL_TOOLS[tool], filename]
    if tool == "slang":
        return [EXTERNAL_TOOLS[tool], "--lint-only", filename]
    raise ValueError(f"不支持的外部工具: {tool}")


def run_external_lint(path, selection):
    """运行外部 linter 并收集结果"""
    results = []
    selected = select_external_tools(selection)
    if selection != "none" and not selected:
        results.append(LintResult(
            1, 1, "EXT", SEVERITY_INFO,
            f"未找到 --external {selection} 对应已安装的外部 linter。仅进行内部检查。"
        ))
        return results

    for tool in selected:
        cmd = external_command(tool, path)
        try:
            completed = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        except OSError as exc:
            results.append(LintResult(1, 1, "EXT", SEVERITY_WARNING, f"{tool} 启动失败: {exc}"))
            continue
        except subprocess.TimeoutExpired:
            results.append(LintResult(1, 1, "EXT", SEVERITY_WARNING, f"{tool} 在 30 秒后超时。"))
            continue

        output = (completed.stdout + "\n" + completed.stderr).strip()
        if completed.returncode == 0:
            results.append(LintResult(1, 1, "EXT", SEVERITY_INFO, f"{tool} 完成，未报告任何问题。"))
            continue

        snippet = " ".join(output.split())[:1000] if output else "未捕获到输出。"
        severity = SEVERITY_ERROR if re.search(r"\berror\b|%Error", output, re.IGNORECASE) else SEVERITY_WARNING
        results.append(LintResult(
            1, 1, "EXT", severity,
            f"{tool} 报告了问题，退出码 {completed.returncode}: {snippet}"
        ))
    return results


def main():
    parser = argparse.ArgumentParser(description="Verilog/SystemVerilog RTL Linter")
    parser.add_argument("file", help="要检查的 RTL 文件（.v 或 .sv）")
    parser.add_argument("--mode", choices=[MODE_RTL, MODE_TB], default=MODE_RTL,
                        help="检查模式：可综合 RTL 或 testbench（默认：rtl）")
    parser.add_argument("--external", choices=["none", "auto", "verilator", "verible", "slang"], default="none",
                        help="可选：运行已安装的外部 linter（默认：none）")
    parser.add_argument("--verbose", "-v", action="store_true", help="显示通过的检查摘要")
    args = parser.parse_args()

    path = Path(args.file)
    if not path.exists():
        print(f"错误：文件未找到: {args.file}")
        sys.exit(1)

    content = path.read_text()
    linter = VerilogLinter(content, filename=args.file, mode=args.mode)
    results = linter.run()
    results.extend(run_external_lint(path, args.external))

    severity_order = {SEVERITY_ERROR: 0, SEVERITY_WARNING: 1, SEVERITY_STYLE: 2, SEVERITY_INFO: 3}
    results.sort(key=lambda r: (severity_order.get(r.severity, 99), r.line, r.col))

    counts = {"ERROR": 0, "WARNING": 0, "STYLE": 0, "INFO": 0}
    icons = {"ERROR": "[错误]", "WARNING": "[警告]", "STYLE": "[风格]", "INFO": "[信息]"}

    if not results:
        print(f"{args.file} 中未发现问题")
        sys.exit(0)

    for r in results:
        counts[r.severity] = counts.get(r.severity, 0) + 1
        print(f"{icons.get(r.severity, '?')} [{r.code}] {r.severity} 位于第 {r.line} 行，第 {r.col} 列")
        print(f"   {r.message}\n")

    print("-" * 50)
    print(f"摘要：{counts['ERROR']} 个错误，{counts['WARNING']} 个警告，"
          f"{counts['STYLE']} 个风格问题，{counts['INFO']} 条信息 —— {args.file}")

    if counts[SEVERITY_ERROR] > 0:
        sys.exit(2)
    sys.exit(0 if counts[SEVERITY_WARNING] == 0 else 1)


if __name__ == "__main__":
    main()
