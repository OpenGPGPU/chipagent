`timescale 1ns / 1ps

module uart_tb;
    logic clk = 0;
    logic rst_n = 0;
    logic reg_write;
    logic [2:0] reg_addr;
    logic [31:0] reg_wdata;
    logic [31:0] reg_rdata;
    logic ctrl_start;
    logic [31:0] status_q;

    uart_reg_top #(.DATA_WIDTH(32)) dut (
        .clk(clk), .rst_n(rst_n), .reg_write(reg_write),
        .reg_addr(reg_addr), .reg_wdata(reg_wdata), .reg_rdata(reg_rdata),
        .ctrl_start(ctrl_start), .status_q(status_q)
    );

    always #5 clk = ~clk;
    integer pass = 1;

    initial begin
        reg_write = 0; reg_addr = 0; reg_wdata = 0;
        #20 rst_n = 1;
        @(negedge clk); reg_write = 1; reg_addr = 0; reg_wdata = 32'h1;
        @(negedge clk); reg_write = 0; #1;
        if (ctrl_start !== 1'b1) begin $display("FAIL: ctrl_start"); pass = 0; end
        reg_addr = 0; #1;
        if (reg_rdata !== 32'h1) begin $display("FAIL: readback"); pass = 0; end
        if (pass) $display("PASS"); else $display("FAIL");
        $finish;
    end
endmodule
