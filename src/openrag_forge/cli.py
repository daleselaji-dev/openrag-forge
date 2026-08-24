import uvicorn


def main() -> None:
    uvicorn.run("openrag_forge.app:app", host="127.0.0.1", port=18000, reload=False)

