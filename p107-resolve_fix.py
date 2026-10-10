p = "tools/ew_loop.py"
L = open(p, newline="").read().split("\n")
a = next(i for i, x in enumerate(L) if x.startswith("<<<<<<<"))
b = next(i for i, x in enumerate(L) if x.startswith(">>>>>>>"))
del L[a:b + 1]
open(p, "w", newline="").write("\n".join(L))
