# About
-------
## What is it?
LineScope is an open-source source profiler for Python. It puts your code at the
center of a readable HTML report, with timing heatmaps, function links, notebook
cells, memory measurements and Spark execution plans. Work inside third-party
libraries stays attributed to the project line that called them. The goal is
simple: find the expensive lines, understand what they do, and make your code
faster. Click [here][getting-started] to get started.
<br>
## What can I do with it?
Profile a script, a package or a notebook, inspect complete source files and
follow functions into their definitions. Choose a collector for your workload,
inspect available memory and GPU metrics, and correlate Spark actions with their
executed plans. Click on the icons to read more about its main functionalities.
<div class="row">
<div class="column">
<div class="icon">
<a
href="../user_guide/backends/#performance" draggable="false"><img
src="../img/icons/performance.svg" alt="Performance"
draggable="false"><figcaption style="margin-top:
-8px"><strong>Performance</strong></figcaption></a></div></div> <div
class="column"><div class="icon"><a href="../user_guide/reports/"
draggable="false"><img src="../img/icons/reports.svg" alt="HTML reports"
draggable="false"><figcaption style="margin-top: -8px"><strong>HTML
reports</strong></figcaption></a></div></div> <div class="column"><div
class="icon"><a href="../user_guide/notebooks/" draggable="false"><img
src="../img/icons/notebooks.svg" alt="Notebooks" draggable="false"><figcaption
style="margin-top: -8px"><strong>Notebooks</strong></figcaption></a></div></div>
<div class="column"><div class="icon"><a href="../user_guide/spark/"
draggable="false"><img src="../img/icons/spark.svg" alt="Spark"
draggable="false"><figcaption style="margin-top:
-8px"><strong>Spark</strong></figcaption></a></div></div>
</div>
<div class="row">
<div class="column"><div class="icon"><a href="../user_guide/gpu/"
draggable="false"><img src="../img/icons/gpu.svg" alt="GPU"
draggable="false"><figcaption style="margin-top:
-8px"><strong>GPU</strong></figcaption></a></div></div> <div class="column"><div
class="icon"><a href="../user_guide/backends/#memory" draggable="false"><img
src="../img/icons/memory.svg" alt="Memory" draggable="false"><figcaption
style="margin-top: -8px"><strong>Memory</strong></figcaption></a></div></div>
<div class="column">
<div class="icon">
<a
href="../user_guide/reports/#source-navigation" draggable="false"><img
src="../img/icons/navigation.svg" alt="Source navigation"
draggable="false"><figcaption style="margin-top: -8px"><strong>Source
navigation</strong></figcaption></a></div></div> <div class="column"><div
class="icon"><a href="../user_guide/backends/" draggable="false"><img
src="../img/icons/backends.svg" alt="Backends" draggable="false"><figcaption
style="margin-top: -8px"><strong>Backends</strong></figcaption></a></div></div>
</div>
## Who is it intended for?
* **Everyday Python users** who want to quickly find bottlenecks and make their
  scripts faster without learning a complex profiling toolchain.
* **Notebook users and data scientists** who want to understand where a cell or
  an entire analysis spends its time.
* **Data engineers** who want to connect Python driver code to Spark actions,
  query plans and the executor metrics their environment makes available.
* **Learners and educators** who want clear, visual feedback about algorithms,
  library calls and memory use. LineScope focuses on practical insights and
  meaningful improvements. Its timings are not a substitute for dedicated
  microbenchmarks when tuning differences of milliseconds down to nanoseconds.
<br>
## Support
LineScope recognizes the support from [JetBrains](https://www.jetbrains.com) by
providing core project contributors with a set of developer tools free of
charge.
<div class="support-logos">
<a href="https://www.jetbrains.com/community/opensource/#support"><img
src="../img/support/jetbrains.png" alt="JetBrains"></a> <a
href="https://www.jetbrains.com/pycharm/"><img src="../img/support/pycharm.png"
alt="PyCharm"></a>
</div>
