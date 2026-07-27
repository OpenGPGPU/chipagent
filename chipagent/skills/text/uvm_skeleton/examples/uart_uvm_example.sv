`timescale 1ns / 1ps

package uart_uvm_pkg;
    class uart_reg_txn;
        rand bit        write;
        rand bit [2:0]  addr;
        rand logic [31:0] data;
    endclass

    class uart_driver;
        virtual interface uart_if vif;
        function void drive(uart_reg_txn txn);
            // placeholder
        endfunction
    endclass

    class uart_basic_seq;
        function void body(ref uart_driver drv);
            uart_reg_txn txn;
            txn = new();
            txn.write = 1; txn.addr = 3'd0; txn.data = 32'h1;
            drv.drive(txn);
            txn.write = 0; txn.addr = 3'd1;
            drv.drive(txn);
        endfunction
    endclass

    class uart_agent;
        uart_driver drv;
    endclass

    class uart_env;
        uart_agent agt;
        function void build();
            agt = new();
        endfunction
        function void run();
            uart_basic_seq seq;
            seq = new();
            seq.body(agt.drv);
        endfunction
    endclass
endpackage : uart_uvm_pkg

interface uart_if;
    logic clk;
    logic rst_n;
    logic reg_write;
    logic [2:0] reg_addr;
    logic [31:0] reg_wdata;
    logic [31:0] reg_rdata;
    logic ctrl_start;
    logic [31:0] status_q;
endinterface

module uart_uvm_tb;
    logic clk = 0;
    logic rst_n = 0;

    uart_if uif();
    assign uif.clk = clk;
    assign uif.rst_n = rst_n;

    uart_reg_top #(.DATA_WIDTH(32)) dut (
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
        import uart_uvm_pkg::*;
        uart_env env;
        env = new();
        env.build();
        #20 rst_n = 1;
        env.run();
        $display("PASS");
        $finish;
    end
endmodule
