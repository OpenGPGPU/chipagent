# Port & comment style

- Use SystemVerilog ANSI port style:
  ```
  module <name> (
      input  logic clk,        // clock, rising-edge
      input  logic rst_n,      // active-low async reset
      ...
  );
  ```
- One port per line. The comma goes **after the declaration, before the
  comment** — never inside the comment.
- Every port has a trailing `//` comment describing its role.
- Add a file-level header comment block before `module` with: Module name,
  Purpose, Interface, Data width, Clocking, Behaviour.
- Inside the module, comment non-obvious blocks (reset path, data path,
  control FSM states).
