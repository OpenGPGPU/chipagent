# Lint rules (mandatory)

Generated code MUST satisfy all of the following. The validator checks them
structurally when no EDA tool is available; verilator/iverilog are used when
present.

1. **module/endmodule balance**: exactly one `module <name>` declaration and
   one `endmodule` per module. No duplicate, no missing.
2. **Balanced parentheses**: `(` and `)` counts must match (after stripping
   comments).
3. **Port separator commas**: in the ANSI port list, every port declaration
   except the last MUST end with a `,` **before** any trailing `//` comment.
   A comma that lands *inside* a `//` comment does not separate ports and is
   a bug. The last port (the one whose next non-empty line is `)`) has no
   comma.
4. **Statement terminators**: every procedural assignment (`<=`) and every
   `assign` statement MUST end with `;`. Declarations (`parameter`,
   `localparam`, `input`, `output`, `inout`) may end with `,` or nothing and
   are not flagged.
5. **No empty module**: the module must contain at least one port or one
   statement.
