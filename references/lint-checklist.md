# RTL Lint 与代码审查清单

## A 类：综合错误（必须修复）

| # | 检查项 | Verilog-2001 | SystemVerilog |
|---|-------|-------------|---------------|
| A1 | 敏感信号列表不完整 | `always @(*)` 或列出所有信号 | 使用 `always_comb` |
| A2 | 同一块中混合阻塞/非阻塞 | 不可混用 `=` 与 `<=` | 规则相同 |
| A3 | case/casex/casez 缺少 default | 添加 `default` 或 `// synthesis full_case` | 添加 `default` |
| A4 | 多驱动网络（多重 assign） | 每个网络一个驱动 | 同上 |
| A5 | 未驱动 / 未使用的网络 | 检查所有输出已驱动，无悬空 | 同上 |
| A6 | 异步复位未列入敏感信号列表 | `always @(posedge clk or negedge rst_n)` | `always_ff` 含异步复位 |
| A7 | ASIC RTL 中出现 `initial` 块 | 移除（testbench 除外） | 移除 |
| A8 | RTL 中出现 `#delay` | 移除（综合忽略） | 移除 |
| A9 | 递归实例化或循环 | 使用 generate/params 打破 | 同上 |
| A10 | 赋值时位宽不匹配 | 显式转换 `$signed()` / `$unsigned()` | 使用 cast 或检查位宽 |
| A11 | 综合代码中含系统任务 | 包裹在 `// synthesis translate_off/on` 中 | `$display`/`$readmemh`/`$monitor` 等 |
| A12 | `posedge clk` 模块未声明 clk 端口 | 将 clk 添加至模块端口列表并正确连接 | 隐式 1-bit wire 导致综合异常 |

## B 类：综合警告（应当修复）

| # | 检查项 | 影响 | 修复方法 |
|---|-------|--------|-----|
| B1 | 锁存器推断 | 意外存储，时序风险 | 组合块中赋默认值 |
| B2 | `return`/`disable` 后不可达代码 | 死代码，覆盖率空洞 | 移除或重构 |
| B3 | 隐式 1 位 wire（未声明） | `default_nettype none` 可捕获 | 显式声明所有信号 |
| B4 | 部分选择越界 | 仿真时出现 X | 边界检查 |
| B5 | 有符号/无符号混用 | 意外截断或符号扩展 | 显式转换 |
| B6 | 除以 2 的幂未用移位 | 面积/性能影响 | 除以 2^N 改用 `>>` |
| B7 | 冗余逻辑（综合优化掉） | 意图不清晰，覆盖率噪声 | 简化表达式 |
| B8 | 时钟用作数据 / 数据用作时钟 | 时序分析失败 | 如需门控时钟，通过 ICG 缓冲 |
| B9 | ROM/RAM initial 块无综合保护 | 综合时存储器内容丢失 | 小型 ROM 用 case 语句；大型 ROM 实例化 IP 宏 |
| B10 | 组合块输出信号未全分支赋值 | 锁存器推断 | 在 always @(*) 块开头对所有输出赋默认值 |

## C 类：ASIC 设计规则（关键）

| # | 检查项 | 为何重要 |
|---|-------|-------------|
| C1 | 无锁存器（除明确设计外） | 锁存器破坏时序收敛，影响 DFT 扫描 |
| C2 | 使用库 ICG 单元做时钟门控 | `enable & clk` 产生毛刺；使用 `CKLNQD` 等 |
| C3 | 所有触发器有复位（或有有效上电状态） | 仿真 X 传播，芯片状态未知 |
| C4 | 无组合逻辑环路 | 振荡，综合失败 |
| C5 | 内部逻辑不推断三态（Z） | STA 分析困难，推荐使用 Mux |
| C6 | 同步复位释放（异步置位可接受） | 需满足复位恢复时序检查 |
| C7 | CDC 信号经过同步器 | 单比特用 2 级同步，总线用握手 |
| C8 | 模块内部无门控时钟（使用使能） | CTS 对门控时钟处理方式不同 |
| C9 | 避免大扇出网络（> 32 典型值） | 插入缓冲树 |
| C10 | 无递归参数循环 | 综合 expand 失败 |

## D 类：风格与可维护性

| # | 检查项 | 建议 |
|---|-------|---------------|
| D1 | 行长度 | 保持 ≤ 120 字符 |
| D2 | Tab/空格一致性 | 4 空格，不用 Tab |
| D3 | 注释密度 | 每个 always 块有意图注释 |
| D4 | 魔法数字 | 使用命名 localparam |
| D5 | 死代码 | 移除注释掉的逻辑 |
| D6 | 端口顺序 | 时钟/复位优先，其次控制，最后数据 |
| D7 | 文件命名 | 与模块名一致：`ModuleName.sv` |
| D8 | Generate 块可读性 | 所有 `begin:` 块加标签 |
| D9 | 嵌套 `if` 深度 | 最多 3-4 层；重构为 case 或独立块 |
| D10 | 跨模块引用 | 避免；使用端口或 bind 断言 |

## CDC（时钟域交叉）检查

- [ ] 识别设计中所有时钟域
- [ ] 单比特 CDC：2 级或 3 级同步器
- [ ] 多比特 CDC：使用握手（req/ack）、FIFO 或 Gray 码指针
- [ ] 复位域交叉：每个域独立的复位同步器
- [ ] 源触发器与同步器之间无组合逻辑
- [ ] 同步后的信号不在多处使用（不复用）

## 断言检查（SystemVerilog）

添加内联断言用于：
- [ ] FSM 中的非法状态：`assert property @(posedge clk) disable iff (!rst_n) (state != STATE_INVALID)`
- [ ] One-hot/有效编码：`assert property @(posedge clk) $onehot(signal)`
- [ ] 输出上无未知值：`assert property @(posedge clk) !$isunknown(data_out)`
- [ ] 协议合规（AXI 握手规则等）

## 审查输出模板

报告审查发现时，按以下分类：
- 🔴 **ERROR（错误）**：综合将失败或产生错误网表
- 🟡 **WARNING（警告）**：综合可完成但存在风险或 QoR 不佳
- 🟢 **STYLE（风格）**：可维护性、可读性、最佳实践
- 🔵 **INFO（建议）**：改进建议，可选优化

## 常用工具对应

| 供应商 | Lint 工具 |
|--------|----------|
| Synopsys | Design Compiler `check_design` + SpyGlass |
| Cadence | Genus `check_design` + HAL |
| Mentor | Questa Lint / RealTime Lint |
| Xilinx | Vivado RTL Analysis |
| 开源 | Verilator --lint-only、Icarus + 自定义脚本 |

## 内置脚本模式

使用附带脚本做快速第一轮检查：

```bash
python scripts/verilog_lint.py design.sv --mode rtl
python scripts/verilog_lint.py tb_design.sv --mode tb
```

- `--mode rtl`：默认；标记 ASIC 不可综合的结构，如 `initial` 和 `#delay`
- `--mode tb`：用于 testbench；允许常见仿真结构，同时保留轻量检查
- `--external auto`：如已安装 `verilator`、`verible-verilog-lint` 或 `slang`，在启发式检查之外运行第一个可用的外部 linter
