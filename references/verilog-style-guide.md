# Verilog & SystemVerilog ASIC 编码风格指南

## 命名规范

| 元素 | 规范 | 示例 |
|---------|-----------|---------|
| 模块 | PascalCase，描述性 | `AxiCrossbar`、`PriorityEncoder` |
| 实例 | snake_case，带 `_i` 后缀 | `crossbar_i`、`clk_gen_i` |
| 端口（输入） | snake_case，方向前缀可选 | `i_data`、`i_valid` 或 `data_in` |
| 端口（输出） | snake_case，方向前缀可选 | `o_result`、`result_out` |
| Wire/Reg/Logic | snake_case | `next_state`、`alu_operand_a` |
| Parameter/Localparam | UPPER_SNAKE_CASE | `DATA_WIDTH`、`ADDR_DEPTH` |
| FSM 状态 | UPPER_SNAKE_CASE 或 CamelCase | `STATE_IDLE`、`StateFetch` |
| 时钟信号 | `clk` 或 `{name}_clk` | `sys_clk`、`axi_clk` |
| 复位信号 | `rst_n`（推荐低有效） | `rst_n`、`sys_rst_n` |
| 高有效复位 | `rst` 并加注释 | `rst` // 高有效复位 |

## 可综合子集规则

### Verilog-2001
- 异步复位触发器使用 `always @(posedge clk or negedge rst_n)`
- 时序 always 块中使用非阻塞赋值 `<=`
- 组合 always 块中使用阻塞赋值 `=`
- RTL 中不使用 `#delay` 语句（仅限 testbench）
- ASIC RTL 中不使用 `initial` 块（FPGA 仅限明确指定时）
- 所有 `case` 语句必须包含 `default` 或 full case 编译指令
- 不使用混合边沿触发（`posedge clk or posedge rst`）

### SystemVerilog 增强
- 时序逻辑使用 `always_ff @(posedge clk or negedge rst_n)`
- 组合逻辑使用 `always_comb`（自动补全敏感信号列表）
- 使用 `logic` 替代 `reg`/`wire`（自动判断方向）
- FSM 状态使用 `enum logic [N:0]`（便于工具调试）
- 使用 `assert` / `assume` / `cover` 进行内联断言（仿真 + 形式验证）
- 数据通路分组使用 `struct packed` / `union packed`
- 复杂总线定义使用 `interface`（AXI、APB 等）

## ASIC 专项指南

### 复位
- ASIC 标准做法：**异步复位 / 同步释放**：
  ```systemverilog
  always_ff @(posedge clk or negedge rst_n) begin
      if (!rst_n) q <= '0;
      else        q <= d;
  end
  ```
- 某些库中同步复位消耗更多门面积；推荐异步。
- 确保复位树平衡并插入缓冲；如有必要说明复位分布。

### 时钟
- 每个 always 块只用一个时钟（不在触发器描述内部做门控）
- 使用库中的门控时钟单元（ICG），而非 `enable & clk`
- 尽量减少时钟域数量；记录所有 CDC 边界

### 避免锁存器
- `always_comb` / `always @(*)` 中赋值不完整会推断出锁存器
- 始终在组合块顶部赋默认值：
  ```systemverilog
  always_comb begin
      next_state = state;  // 默认值
      case (state)
          // ...
      endcase
  end
  ```

### 综合编译指令
- 尽量少用 `// synthesis full_case parallel_case`；优先使用显式代码
- 使用 `// synthesis translate_off/on` 包裹仅仿真代码（断言、调试）
- 使用 `(* dont_touch = "true" *)` / `(* keep = "true" *)` 保留层次结构（供应商相关）

## 代码结构

1. **文件头注释**：模块名、作者、描述、修订历史
2. **时间刻度**：`timescale 1ns / 1ps`（testbench）或在 RTL 中省略（由工具管理）
3. **参数**：在端口之前声明，带默认值
4. **端口**：每行一个，含方向、位宽和注释
5. **内部信号**：按功能分组（控制、数据通路、调试）
6. **Always 块**：每个时钟域独立的 always_ff，组合逻辑独立 always_comb
7. **结尾**：`endmodule` 后附带模块名注释：`endmodule // ModuleName`

## 示例模板

```systemverilog
module MyModule #(
    parameter int DATA_WIDTH = 32,
    parameter int ADDR_WIDTH = 8
) (
    input  logic                  clk,
    input  logic                  rst_n,
    input  logic [ADDR_WIDTH-1:0] i_addr,
    input  logic [DATA_WIDTH-1:0] i_wdata,
    input  logic                  i_wr_en,
    output logic [DATA_WIDTH-1:0] o_rdata,
    output logic                  o_valid
);

    // 内部信号
    logic [DATA_WIDTH-1:0] mem [0:(1<<ADDR_WIDTH)-1];
    logic [DATA_WIDTH-1:0] rdata_next;

    // 时序逻辑
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            o_valid <= 1'b0;
        end else begin
            o_valid <= i_wr_en;
            if (i_wr_en) mem[i_addr] <= i_wdata;
        end
    end

    // 组合逻辑读
    always_comb begin
        rdata_next = mem[i_addr];
    end

    assign o_rdata = rdata_next;

endmodule // MyModule
```
