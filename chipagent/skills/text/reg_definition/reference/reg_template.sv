// Register-block template baseline. ChipAgent fills {{module_name}} and
// {{data_width}}; address width is fixed small (enough for a handful of regs).
//------------------------------------------------------------
// Module : {{module_name}}_reg_top
// Purpose: Memory-mapped register block with CPU read/write decode.
// Data width: {{data_width}} bits
// Clocking: rising-edge clk, async active-low reset (rst_n).
// Registers: CTRL (offset 0), STATUS (offset 1).
//------------------------------------------------------------
module {{module_name}}_reg_top #(
    parameter int DATA_WIDTH = {{data_width}}
)(
    input  logic                  clk,        // clock, rising-edge
    input  logic                  rst_n,      // active-low asynchronous reset
    // Simple CPU bus interface
    input  logic                  reg_write,  // write strobe
    input  logic [2:0]            reg_addr,   // register address
    input  logic [DATA_WIDTH-1:0] reg_wdata,  // write data
    output logic [DATA_WIDTH-1:0] reg_rdata,  // read data
    // Hardware-visible register fields
    output logic                  ctrl_start, // CTRL.start
    output logic [DATA_WIDTH-1:0] status_q    // STATUS raw value
);
    localparam logic [2:0] ADDR_CTRL   = 3'd0;  // offset 0
    localparam logic [2:0] ADDR_STATUS = 3'd1;  // offset 1

    logic [DATA_WIDTH-1:0] ctrl_reg;
    logic [DATA_WIDTH-1:0] status_reg;

    // Write decode
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            ctrl_reg   <= '0;
            status_reg <= '0;
        end else if (reg_write) begin
            case (reg_addr)
                ADDR_CTRL:   ctrl_reg   <= reg_wdata;
                ADDR_STATUS: status_reg <= reg_wdata;
                default: ;
            endcase
        end
    end

    // Read decode
    always_comb begin
        case (reg_addr)
            ADDR_CTRL:   reg_rdata = ctrl_reg;
            ADDR_STATUS: reg_rdata = status_reg;
            default:     reg_rdata = '0;
        endcase
    end

    assign ctrl_start = ctrl_reg[0];   // CTRL.start field
    assign status_q   = status_reg;
endmodule
