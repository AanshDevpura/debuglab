import os

if os.environ.get("DEBUGLAB_HOSTED_SPENDING_ROOT"):
    import httpx
    from hosted_guard import install

    httpx.post = install(os.environ["DEBUGLAB_HOSTED_SPENDING_ROOT"], httpx.post)
