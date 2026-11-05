"""Writing Nuke script (.nk) text: quoting values the way Nuke's own script writer does."""


def q(s):
    """A string as a double-quoted .nk value: backslash, quotes, brackets, braces and $ escaped (Tcl would otherwise
    run [commands] and expand $variables), newlines as \\n."""
    out = []
    for ch in s:
        if ch == '\\':
            out.append('\\\\')
        elif ch == '"':
            out.append('\\"')
        elif ch in '[]{}$':
            out.append('\\' + ch)
        elif ch == '\n':
            out.append('\\n')
        elif ch == '\t':
            out.append('  ')
        else:
            out.append(ch)
    return '"' + ''.join(out) + '"'


def word(s):
    """A label or menu item: bare if it is one plain word, quoted otherwise."""
    s = str(s)
    if s and all(c.isalnum() or c in '_.-+' for c in s):
        return s
    return q(s)


def num(v):
    if isinstance(v, bool):
        return 'true' if v else 'false'
    if isinstance(v, int):
        return str(v)
    r = repr(float(v))
    return r[:-2] if r.endswith('.0') else r
