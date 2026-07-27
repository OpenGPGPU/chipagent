`timescale 1ns / 1ps

module {{module_name}}_tb;
    logic clk = 0;
    logic rst_n = 0;
    logic reg_write;
    logic [2:0] reg_addr;
    logic [{{data_width_m1}}:0] reg_wdata;
    logic [{{data_width_m1}}:0] reg_rdata;
    logic ctrl_start;
    logic [{{data_width_m1}}:0] status_q;

    {{module_name}}_reg_top #(.DATA_WIDTH({{data_width}})) dut (
        .clk(clk),
        .rst_n(rst_n),
        .reg_write(reg_write),
        .reg_addr(reg_addr),
        .reg_wdata(reg_wdata),
        .reg_rdata(reg_rdata),
        .ctrl_start(ctrl_start),
        .status_q(status_q)
    );

    always #5 clk = ~clk;

    integer pass = 1;

    initial begin
        reg_write = 0; reg_addr = 0; reg_wdata = 0;
        #20 rst_n = 1;

        // Write CTRL = 1 (addr 0)
        @(negedge clk);
        reg_write = 1; reg_addr = 3'd0; reg_wdata = {{data_width}}'h1;
        @(negedge clk);
        reg_write = 0;
        #1;
        if (ctrl_start !== 1'b1) begin
            $display("FAIL: ctrl_start not asserted after CTRL write");
            pass = 0;
        end

        // Read CTRL back
        reg_addr = 3'd0;
        #1;
        if (reg_rdata !== {{data_width}}'h1) begin
            $display("FAIL: CTRL readback mismatch, got %h", reg_rdata);
            pass = 0;
        end

        if (pass) $display("PASS");
        else $display("FAIL");
        $finish;
    end
endmodule
