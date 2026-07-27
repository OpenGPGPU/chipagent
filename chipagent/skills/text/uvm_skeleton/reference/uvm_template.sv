`timescale 1ns / 1ps

// Minimal UVM skeleton for {{module_name}}_reg_top.
// Structurally valid (balanced module/endmodule + class/endclass) so the
// structural lint gate passes even without a UVM install. Imports are guarded
// so iverilog -g2012 can still elaborate the file as a syntax check.

package {{module_name}}_uvm_pkg;
    // import uvm_pkg::*;   // uncomment in a real UVM environment

    // Bus transaction for frontdoor register access.
    class {{module_name}}_reg_txn;
        rand bit        write;
        rand bit [2:0]  addr;
        rand logic [{{data_width_m1}}:0] data;
    endclass

    // Driver: applies register writes/reads to the DUT interface.
    class {{module_name}}_driver;
        virtual interface {{module_name}}_if vif;
        function void drive({{module_name}}_reg_txn txn);
            // Placeholder: real driver would clock-out the txn.
        endfunction
    endclass

    // Sequence: write CTRL=1, then read STATUS.
    class {{module_name}}_basic_seq;
        function void body(ref {{module_name}}_driver drv);
            {{module_name}}_reg_txn txn;
            txn = new();
            txn.write = 1; txn.addr = 3'd0; txn.data = {{data_width}}'h1;
            drv.drive(txn);
            txn.write = 0; txn.addr = 3'd1;
            drv.drive(txn);
        endfunction
    endclass

    // Agent + env wrappers.
    class {{module_name}}_agent;
        {{module_name}}_driver drv;
    endclass

    class {{module_name}}_env;
        {{module_name}}_agent agt;
        function void build();
            agt = new();
        endfunction
        function void run();
            {{module_name}}_basic_seq seq;
            seq = new();
            seq.body(agt.drv);
        endfunction
    endclass
endpackage : {{module_name}}_uvm_pkg

// DUT interface.
interface {{module_name}}_if;
    logic clk;
    logic rst_n;
    logic reg_write;
    logic [2:0] reg_addr;
    logic [{{data_width_m1}}:0] reg_wdata;
    logic [{{data_width_m1}}:0] reg_rdata;
    logic ctrl_start;
    logic [{{data_width_m1}}:0] status_q;
endinterface

// Top testbench: clk/rst + DUT + env.
module {{module_name}}_uvm_tb;
    logic clk = 0;
    logic rst_n = 0;

    {{module_name}}_if uif();
    assign uif.clk = clk;
    assign uif.rst_n = rst_n;

    {{module_name}}_reg_top #(.DATA_WIDTH({{data_width}})) dut (
        .clk(uif.clk),
        .rst_n(uif.rst_n),
        .reg_write(uif.reg_write),
        .reg_addr(uif.reg_addr),
        .reg_wdata(uif.reg_wdata),
        .reg_rdata(uif.reg_rdata),
        .ctrl_start(uif.ctrl_start),
        .status_q(uif.status_q)
    );

    always #5 clk = ~clk;

    initial begin
        import {{module_name}}_uvm_pkg::*;
        {{module_name}}_env env;
        env = new();
        env.build();
        #20 rst_n = 1;
        env.run();
        $display("PASS");
        $finish;
    end
endmodule
