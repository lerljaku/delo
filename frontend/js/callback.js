import { completeLogin, showError } from "./common.js";

completeLogin()
  .then((returnTo) => location.replace(returnTo))
  .catch((err) => showError(document.getElementById("content"), err));
