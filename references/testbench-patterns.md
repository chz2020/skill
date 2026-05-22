# Testbench 模式参考

## Testbench 架构

### 1. 简易定向 Testbench（Verilog-2001）

适用场景：小型模块、快速功能检查、单元测试。

```verilog
`timescale 1ns / 1ps

module tb_simple;
    // 参数
    parameter CLK_PERIOD = 10; // 100MHz

    // 信号
    reg  clk = 0;
    reg  rst_n;
    reg  [7:0] din;
    wire [7:0] dout;

    // 时钟发生器
    always #(CLK_PERIOD/2) clk = ~clk;

    // DUT 实例化
    my_dut u_dut (
        .clk   (clk),
        .rst_n (rst_n),
        .din   (din),
        .dout  (dout)
    );

    // 激励
    initial begin
        $dumpfile("waveform.vcd");
        $dumpvars(0, tb_simple);

        // 复位
        rst_n = 0;
        din   = 0;
        #20;
        rst_n = 1;
        #10;

        // 定向测试
        din = 8'hAA; #10;
        din = 8'h55; #10;
        din = 8'hFF; #10;
        din = 8'h00; #10;

        // 检查结果
        #10;
        if (dout !== expected_val)
            $display("ERROR at %0t: expected %h, got %h", $time, expected_val, dout);
        else
            $display("PASS at %0t", $time);

        #20;
        $finish;
    end
endmodule
```

### 2. 自检 Testbench（SystemVerilog）

适用场景：输出可预测的模块、算法类模块。

```systemverilog
module tb_selfcheck;
    import uvm_pkg::*;

    localparam CLK_PERIOD = 10;
    logic clk = 0, rst_n;

    // 接口
    axi_lite_if axi_if (.clk(clk), .rst_n(rst_n));

    // DUT
    dut u_dut (.axi(axi_if));

    always #(CLK_PERIOD/2) clk = ~clk;

    // Scoreboard 式检查器
    task automatic run_test();
        logic [31:0] expected_queue[$];
        // ... 生成事务，推入期望结果
        // 在输出端比较
    endtask

    initial begin
        $dumpfile("waves.vcd");
        $dumpvars();
        rst_n = 0;
        #50 rst_n = 1;
        run_test();
        #100 $finish;
    end
endmodule
```

### 3. UVM 组件式 Testbench（SystemVerilog）

适用场景：复杂 SoC 验证、可复用 VIP、覆盖率驱动的验证。

架构：
- **Sequence item**：事务定义（地址、数据、操作）
- **Sequencer**：从 sequence 中取出 sequence item，传递给 driver
- **Driver**：将 sequence item 转换为接口上的引脚时序
- **Monitor**：观察接口，发送事务到 scoreboard/coverage
- **Agent**：封装 driver、sequencer、monitor
- **Scoreboard**：预测期望结果，与实际结果比对
- **Environment**：包含 agent、scoreboard、coverage
- **Test**：配置 environment，启动 sequence

```systemverilog
// 示例：最小 UVM 测试模板
class my_test extends uvm_test;
    `uvm_component_utils(my_test)
    my_env env;

    function new(string name, uvm_component parent);
        super.new(name, parent);
    endfunction

    function void build_phase(uvm_phase phase);
        env = my_env::type_id::create("env", this);
    endfunction

    task run_phase(uvm_phase phase);
        my_sequence seq;
        phase.raise_objection(this);
        seq = my_sequence::type_id::create("seq");
        seq.start(env.agent.sequencer);
        phase.drop_objection(this);
    endtask
endclass
```

## 接口模板

### AXI4-Lite 接口

```systemverilog
interface axi_lite_if #(parameter ADDR_WIDTH = 32, DATA_WIDTH = 32)(
    input logic clk,
    input logic rst_n
);
    logic                  awvalid;
    logic                  awready;
    logic [ADDR_WIDTH-1:0] awaddr;
    logic [2:0]            awprot;

    logic                  wvalid;
    logic                  wready;
    logic [DATA_WIDTH-1:0] wdata;
    logic [(DATA_WIDTH/8)-1:0] wstrb;

    logic                  bvalid;
    logic                  bready;
    logic [1:0]            bresp;

    logic                  arvalid;
    logic                  arready;
    logic [ADDR_WIDTH-1:0] araddr;
    logic [2:0]            arprot;

    logic                  rvalid;
    logic                  rready;
    logic [DATA_WIDTH-1:0] rdata;
    logic [1:0]            rresp;

    modport master (
        output awvalid, awaddr, awprot,
        input  awready,
        output wvalid, wdata, wstrb,
        input  wready,
        input  bvalid, bresp,
        output bready,
        output arvalid, araddr, arprot,
        input  arready,
        input  rvalid, rdata, rresp,
        output rready
    );

    modport slave (
        input  awvalid, awaddr, awprot,
        output awready,
        input  wvalid, wdata, wstrb,
        output wready,
        output bvalid, bresp,
        input  bready,
        input  arvalid, araddr, arprot,
        output arready,
        output rvalid, rdata, rresp,
        input  rready
    );
endinterface
```

### APB 接口

```systemverilog
interface apb_if #(parameter ADDR_WIDTH = 16, DATA_WIDTH = 32)(
    input logic pclk,
    input logic preset_n
);
    logic [ADDR_WIDTH-1:0] paddr;
    logic                  psel;
    logic                  penable;
    logic                  pwrite;
    logic [DATA_WIDTH-1:0] pwdata;
    logic [DATA_WIDTH-1:0] prdata;
    logic                  pready;
    logic                  pslverr;
endinterface
```

## 覆盖率收集

ASIC sign-off 需要收集：
- **代码覆盖率**：行、翻转、状态机、分支、表达式
- **功能覆盖率**：关键功能的交叉覆盖
- **断言覆盖率**：SVA 断言通过/失败计数

```systemverilog
// 功能覆盖率示例
covergroup cg_axi @(posedge clk);
    cp_addr: coverpoint awaddr {
        bins low  = {[0:32'h0FFF]};
        bins mid  = {[32'h1000:32'h7FFF]};
        bins high = {[32'h8000:$]};
    }
    cp_prot: coverpoint awprot;
    cross cp_addr, cp_prot;
endgroup
```

## 波形 Dump 技巧

- VCD：通用但文件大；适合小规模仿真
- FSDB：Verdi 格式，压缩，大型设计推荐
- SHM：Cadence 格式

```systemverilog
// 条件 dump（节省磁盘空间）
initial begin
    if ($test$plusargs("DUMP")) begin
        $dumpfile("waves.vcd");
        $dumpvars(0, tb_top);
    end
end
```

## Testbench 检查清单

- [ ] 时钟和复位生成已验证（复位同步释放）
- [ ] 所有 DUT 输入已驱动（无不驱动输入导致的 X 传播）
- [ ] 测试覆盖复位、正常操作、边界情况、错误情况
- [ ] 自检或 scoreboard 式比对（非人工查看波形）
- [ ] 覆盖率收集已启用
- [ ] 超时看门狗防止仿真卡死
- [ ] 随机种子可配置，保证可复现
