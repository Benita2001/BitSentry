def main():
    import uvicorn
    import os
    from dotenv import load_dotenv
    load_dotenv()
    print("Starting BitSentry server on http://127.0.0.1:8000")
    uvicorn.run("bitsentry.api.server:app", host="127.0.0.1", port=8000, reload=False)

if __name__ == "__main__":
    main()
