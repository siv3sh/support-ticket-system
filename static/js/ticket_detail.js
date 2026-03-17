document.addEventListener("DOMContentLoaded", function () {
  var btn = document.getElementById("suggest-reply-btn");
  var textarea = document.getElementById("reply-body");
  var msg = document.getElementById("suggest-msg");

  if (!btn || !textarea || !msg) return;

  var suggestUrl = btn.getAttribute("data-suggest-url");
  if (!suggestUrl) return;

  btn.addEventListener("click", function () {
    btn.disabled = true;
    msg.textContent = "Generating suggestion…";

    fetch(suggestUrl)
      .then(function (r) {
        return r.json().then(function (data) {
          if (!r.ok) {
            var e = new Error(
              data.error || (r.status === 503 ? "Service unavailable" : "Request failed")
            );
            e.data = data;
            throw e;
          }
          return data;
        });
      })
      .then(function (data) {
        if (data.suggested_reply) {
          textarea.value = data.suggested_reply;
          msg.textContent = "Suggestion inserted. Edit if needed and send.";
        } else {
          msg.textContent = data.error || "Could not generate suggestion.";
        }
      })
      .catch(function (e) {
        msg.textContent =
          e && e.data && e.data.error
            ? e.data.error
            : (e && e.message) || "Could not generate suggestion. Check your connection or try again.";
      })
      .finally(function () {
        btn.disabled = false;
      });
  });
});

