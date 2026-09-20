# Fonts note

`docker/fonts/DejaVuSans.ttf` is the fallback font path used by `font_resolver.resolve_font_path`
when a requested typeface cannot be found on the system. The actual font file is not bundled in
this repository — download DejaVu Sans (SIL license, https://dejavu-fonts.github.io/) and place
`DejaVuSans.ttf` in `docker/fonts/` before relying on font-rendering features that need it (e.g.
autofit in a later sprint).
