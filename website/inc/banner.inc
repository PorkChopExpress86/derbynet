<?php
// GENERATED FILE; DO NOT EDIT DIRECTLY. See templates/banner.inc

// Back button is false for no back button, or the name of the "back" page.
// * back_button must be one of index.php, setup.php.
function make_banner($banner_title, $back_button = 'index.php') {
  echo "<header class=\"banner\" role=\"banner\">\n";

  if ($back_button && in_array($back_button, ['index.php', 'setup.php', 'checkin.php',
                                              'kiosk-dashboard.php', 'coordinator.php'])) {
    echo "<button type=\"button\" class=\"back_button\" onclick=\"window.location='".$back_button."'; return false;\"";
    echo " aria-label=\"Back\">\n";
    echo "<img src=\"img/back-button.png\" alt=\"\"/>\n";
    echo "<span>Back</span>\n";
    echo "</button>\n";
  } else {
    echo "<span class=\"back_button back_button--placeholder\" aria-hidden=\"true\"></span>\n";
  }

  echo "<h1 class=\"banner_title\">".htmlspecialchars($banner_title, ENT_QUOTES, 'UTF-8')."</h1>\n";

  echo "<div class=\"banner_actions\">\n";
  if (isset($_SESSION['role']) && $_SESSION['role']) {
    echo "<a class=\"help\" href='quickref.pdf' target='derbynet_help' rel='noreferrer noopener'";
    echo " aria-label=\"Open DerbyNet quick reference\">";
    echo "<img src=\"img/help.png\" alt=\"Help icon\"/>";
    echo "<span class=\"help__label\">@BRANCH@</span>";
    echo "</a>\n";
  }
  echo "<img class=\"logo\" src=\"img/derbynet.png\" alt=\"DerbyNet logo\"/>\n";
  echo "</div>\n";

  echo "</header>\n";
}
?>
