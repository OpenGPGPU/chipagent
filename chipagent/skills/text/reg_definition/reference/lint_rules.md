# Lint rules (mandatory)

Generated register-block code MUST satisfy all of the following.

1. **module/endmodule balance**: exactly one `module <name>` and one `endmodule`.
2. **Balanced parentheses**: `(` and `)` counts must match (after stripping
   comments).
3. **Port separator commas**: every port except the last must end with a `,`
   **before** any trailing `//` comment. The last port (next non-empty line is
   `)`) has no comma.
4. **Statement terminators**: every `<=`, `=`, and `assign` statement MUST end
   with `;`. `case` label lines (`ADDR_X: <stmt>;`) must terminate with `;`;
   `default: ;` is allowed.
5. **`case`/`endcase` balance**: every `case` has a matching `endcase`.
6. **No empty module**: the block must contain the write and read decode
   `always_ff`/`always_comb` blocks.
