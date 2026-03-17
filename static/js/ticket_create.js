document.addEventListener("DOMContentLoaded", function () {
  var customer = document.getElementById("customer");
  var companyId = document.getElementById("company_id");
  var customerId = document.getElementById("customer_id");
  var form = document.querySelector("form");

  if (!customer || !companyId || !customerId || !form) return;

  function syncHiddenFields() {
    var v = customer.value || "";
    if (v) {
      var parts = v.split("||");
      companyId.value = parts[0] || "";
      customerId.value = parts[1] || "";
    } else {
      companyId.value = "";
      customerId.value = "";
    }
  }

  customer.addEventListener("change", syncHiddenFields);
  form.addEventListener("submit", syncHiddenFields);
});

