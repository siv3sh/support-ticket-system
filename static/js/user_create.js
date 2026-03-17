document.addEventListener("DOMContentLoaded", function () {
  var sendTemp = document.getElementById("send_temp");
  var passwordGroup = document.getElementById("password-group");
  var passwordInput = document.getElementById("password");

  if (!sendTemp || !passwordGroup || !passwordInput) return;

  function toggle() {
    if (sendTemp.checked) {
      passwordGroup.style.display = "none";
      passwordInput.removeAttribute("required");
    } else {
      passwordGroup.style.display = "block";
      passwordInput.setAttribute("required", "required");
    }
  }

  sendTemp.addEventListener("change", toggle);
  toggle();
});

