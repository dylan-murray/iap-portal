import gradio as gr

from iap_portal.auth.core import user_from_request


def greet(name: str, request: gr.Request):
    user = user_from_request(dict(request.headers))
    return f"Hello {name} — signed in as {user.email}"


demo = gr.Interface(fn=greet, inputs="text", outputs="text")

if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=8080)
