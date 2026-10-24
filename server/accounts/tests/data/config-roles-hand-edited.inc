<?php
// Edited by hand: integer masks, a short array, comments, and new passwords.
$roles = array('' => array('password' => '', 'permissions' => 0),
               "Timer" => ['password' => '', 'permissions' => 4096, 'interactive' => false],
               'Judge' => array('password' => 'it\'s a \\secret',
                                'permissions' => 256 | VIEW_AWARDS_PERMISSION),  # ballots
               'RaceCrew' => array('password' => '10',
                                   'permissions' => 8687,
                                   'interactive' => true,),
               /* full control */
               'RaceCoordinator' => array('password' => 'n3w-pass',
                                          'permissions' => -1),
              );
$post_setup_role = "RaceCrew";
?>
