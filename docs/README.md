# Paper

Read `verify-and-repair-paper.pdf`. The editable source is
`verify-and-repair-paper.tex`; the algorithms use the real LaTeX `algorithm2e`
package.

The text uses only two core symbols: **P** for code and **F** for public
failures. Examples explain the workflow before discussing implementation
boundaries and results. The historical experiment is distinguished from the
fresh Copilot demonstration.
The normal-agent extension is described separately: ordinary generation,
verification at testing checkpoints and automatic counterexample repair within
a task's approved budget. Same-agent MCP feedback is documented in
[the harness guide](harnesses.md). Historical benchmarks do not evaluate it.

## Compile the included source

Install a LaTeX distribution such as MiKTeX or TeX Live, with `algorithm2e`,
TikZ, listings, booktabs, microtype and the standard math/font packages.
Then run from this directory:

```powershell
pdflatex -interaction=nonstopmode -halt-on-error -no-shell-escape verify-and-repair-paper.tex
pdflatex -interaction=nonstopmode -halt-on-error -no-shell-escape verify-and-repair-paper.tex
```

LaTeX is needed only to rebuild the PDF, not to install or run the coding
controller. Dependencies retain their own licenses.
