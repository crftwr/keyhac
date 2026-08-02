---
layout: default
title: keyhac/_config.py
permalink: /keyhac/_config.py/
---
{% comment %}
GitHub Pages only. README.md and doc/configuration.md link to the config
template with repo-relative targets, which GitHub resolves to the
syntax-highlighted blob view at whatever ref is being browsed, and
gen_pypi_readme.py rewrites to version-tagged URLs for PyPI — but the Pages
site would serve the bare .py file as a download. This page claims that URL
on Pages instead (/keyhac/_config.py 301s to /keyhac/_config.py/) and
renders the template with highlighting.

The include below resolves because _config.yml sets includes_dir: keyhac —
Liquid's include_relative cannot reach ../keyhac from doc/. Constraints:
- _config.py must NOT be forced into the site via `include:` in _config.yml;
  the raw file would collide with this page's output directory.
- The include passes the template through Liquid, so it must stay free of
  {{ "{{" }} and {{ "{%" }} sequences (it has none today).
{% endcomment %}

The shipped configuration template, copied to `~/.keyhac/config.py` on first
run. The [configuration reference]({{ '/doc/configuration.html' | relative_url }})
documents everything it shows, or
[view it on GitHub]({{ site.github.repository_url }}/blob/main/keyhac/_config.py).

{% highlight python %}
{% include _config.py %}
{% endhighlight %}
