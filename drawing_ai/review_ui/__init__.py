"""Human-review gate tooling: turns already-extracted ground truth into the
data these review pages need, and renders the pages themselves.

Why this package exists: every stage of this pipeline that isn't yet at
production-grade accuracy (character/number identification, dimension
arithmetic, 3D wall reconstruction) needs a human confirmation step before
its output is trusted downstream -- see README.md section 3.10. This
package is the data + rendering side of that gate; it does not decide *when*
a run is human-reviewed (that's an orchestrator-level workflow concern, not
yet wired in) -- see the module docstrings below for what's implemented
versus still a TODO.
"""
