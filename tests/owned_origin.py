from urllib.parse import parse_qs

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse


app = FastAPI(title="RelayNorth Owned Test Origin")


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> str:
    cookie = request.cookies.get("relay_test") or "not set"
    return f"""
    <!doctype html>
    <html lang="en">
      <head><meta charset="utf-8"><title>Owned proxy test origin</title></head>
      <body>
        <main>
          <h1>Owned proxy test origin</h1>
          <p>Cookie value: <strong>{cookie}</strong></p>
          <nav>
            <a href="/cookie">Set a test cookie</a>
            <a href="/redirect">Test a redirect</a>
          </nav>
          <form action="/echo" method="post">
            <label>Message <input name="message" value="hello from the test site"></label>
            <button type="submit">Submit a safe POST</button>
          </form>
        </main>
      </body>
    </html>
    """


@app.get("/cookie")
async def set_cookie() -> RedirectResponse:
    response = RedirectResponse(url="/", status_code=303)
    response.set_cookie("relay_test", "isolated", httponly=True, samesite="lax")
    return response


@app.get("/redirect")
async def redirect_home() -> RedirectResponse:
    return RedirectResponse(url="/", status_code=302)


@app.get("/redirect-private")
async def redirect_private() -> RedirectResponse:
    return RedirectResponse(url="http://169.254.169.254/latest/meta-data/", status_code=302)


@app.get("/redirect-external")
async def redirect_external() -> RedirectResponse:
    return RedirectResponse(url="https://example.com/", status_code=302)


@app.post("/echo", response_class=HTMLResponse)
async def echo(request: Request) -> str:
    form = parse_qs((await request.body()).decode("utf-8", errors="replace"))
    message = form.get("message", [""])[0]
    safe_message = message.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return f"<main><h1>POST received</h1><p>{safe_message}</p><a href='/'>Back</a></main>"
