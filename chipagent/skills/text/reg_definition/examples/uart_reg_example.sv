// Example output of the reg_definition skill: a small UART register block.
// Style/quality reference only — do not copy verbatim.
`ifndef UART_REG_VH
`define UART_REG_VH
localparam logic [2:0] UART_CTRL       = 3'd0;  // [0] enable, [1] tx_start
localparam logic [2:0] UART_STATUS     = 3'd1;  // [0] tx_busy, [1] rx_valid
localparam logic [2:0] UART_BAUD_DIV   = 3'd2;  // baud divider
`endif

module uart_reg_top #(
    parameter int DATA_WIDTH = 32
)(
    input  logic                  clk,
    input  logic                  rst_n,
    input  logic                  reg_write,
    input  logic [2:0]            reg_addr,
    input  logic [DATA_WIDTH-1:0] reg_wdata,
    output logic [DATA_WIDTH-1:0] reg_rdata,
    output logic                  ctrl_enable,
    output logic                  tx_start,
    output logic                  tx_busy,
    output logic                  rx_valid
);
    localparam logic [2:0] ADDR_CTRL   = 3'd0;
    localparam logic [2:0] ADDR_STATUS = 3'd1;

    logic [DATA_WIDTH-1:0] ctrl_reg;
    logic [DATA_WIDTH-1:0] status_reg;

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

    always_comb begin
        case (reg_addr)
            ADDR_CTRL:   reg_rdata = ctrl_reg;
            ADDR_STATUS: reg_rdata = status_reg;
            default:     reg_rdata = '0;
        endcase
    end

    assign ctrl_enable = ctrl_reg[0];
    assign tx_start    = ctrl_reg[1];
    assign tx_busy     = status_reg[0];
    assign rx_valid    = status_reg[1];
endmodule
