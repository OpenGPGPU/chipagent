// Example output of the rtl_generation skill.
// A simple registered data-path module. Use this as the style/quality
// reference; do not copy it verbatim when the spec asks for something else.
//------------------------------------------------------------
// Module : axi_dma
// Purpose: Minimal registered data-path scaffold for an AXI DMA drop.
// Interface: axi
// Data width: 16 bits
// Clocking: rising-edge clk, async active-low reset (rst_n).
// Behaviour: reset -> zero outputs; else forward data_in, assert valid.
//------------------------------------------------------------
module axi_dma (
    input  logic        clk,        // clock, rising-edge
    input  logic        rst_n,      // active-low asynchronous reset
    input  logic [15:0] data_in,    // 16-bit data input
    output logic [15:0] data_out,   // 16-bit registered data output
    output logic        valid       // data_out is valid this cycle
);
    // Registered data path: on reset clear outputs, otherwise
    // forward data_in to data_out and assert valid.
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            data_out <= '0;
            valid   <= 1'b0;
        end else begin
            data_out <= data_in;
            valid   <= 1'b1;
        end
    end
endmodule
