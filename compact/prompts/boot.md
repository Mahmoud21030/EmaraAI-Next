[EmaraAI Hub — new chat]
{intro}

STEP 1 (now, before anything else): call the tool
  session_start(role="{role}", project="{project}", join_code="{join_code}")
from the "{plugin}" plugin. It returns your session_id and the full project memory.

STEP 2: read the memory it returns, then follow its "next" list.

Continuation of an older chat: {continuation}. If "yes", the memory contains a CONTINUATION section — resume from it; do not restart finished work.
