#!/usr/bin/env bash
# Source from the repository root. Prefer this workspace's installed tools when present.
if [ -d "$PWD/work/tools/env" ]; then
  export JAVA_HOME="$PWD/work/tools/env/lib/jvm"
  export PATH="$JAVA_HOME/bin:$PWD/work/tools/env/bin:$PATH"
fi
if [ -d "$PWD/work/tools/defects4j" ]; then
  export PATH="$PWD/work/tools/defects4j/framework/bin:$PATH"
fi
if [ -d "$PWD/work/tools/perl/lib/perl5" ]; then
  export PERL5LIB="$PWD/work/tools/perl/lib/perl5${PERL5LIB:+:$PERL5LIB}"
fi
if [ -d "$PWD/work/tools/huggingface" ]; then
  export HF_HOME="$PWD/work/tools/huggingface"
fi
# The native benchmark protocol fixes the process timezone for reproducibility.
export TZ=America/Los_Angeles
