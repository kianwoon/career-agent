# CFPreferences dict values as NSString break Swift as?-cast — the resurfacing 'settings gone' master cause

- **Date**: 2026-08-27T12:20:48+0800
- **Type**: lesson

## What happened

2026-08-27 Mousecape: user's custom cursor scales repeatedly 'disappeared' in the GUI after every fix. Final root cause: MCPerCursorScales was at different times written by (a) Swift CFPreferencesSetAppValue with [String:Double] → NSNumber values, and (b) Command line interface to a user's defaults.
Syntax:

'defaults' [-verbose] [-currentHost | -host <hostname>] [-container <container identifier>] followed by one of the following:

  read                                 shows all defaults
  read <domain>                        shows defaults for given domain
  read <domain> <key>                  shows defaults for given domain, key

  read-type <domain> <key>             shows the type for the given domain, key

  write <domain> <domain_rep>          writes domain (overwrites existing)
  write <domain> <key> <value>         writes key for domain

  rename <domain> <old_key> <new_key>  renames old_key to new_key

  delete <domain>                      deletes domain
  delete <domain> <key>                deletes key in domain
  delete-all <domain>                  deletes the domain from all containers
  delete-all <domain> Key>             deletes key in domain from all containers

  import <domain> <path to plist>      writes the plist at path to domain
  import <domain> -                    writes a plist from stdin to domain
  export <domain> <path to plist>      saves domain as a binary plist to path
  export <domain> -                    writes domain as an xml plist to stdout
  domains                              lists all domains
  find <word>                          lists all entries containing word
  help                                 print this help

<domain> is ( <domain_name> | -app <application_name> | -globalDomain )
         or a path to a file omitting the '.plist' extension

<value> is one of:
  <value_rep>
  -string <string_value>
  -data <hex_digits>
  -int[eger] <integer_value>
  -float  <floating-point_value>
  -bool[ean] (true | false | yes | no)
  -date <date_rep>
  -array <value1> <value2> ...
  -array-add <value1> <value2> ...
  -dict <key1> <value1> <key2> <value2> ...
  -dict-add <key1> <value1> ... / python scripts → NSString values. Swift readers used strict  — NSString values fail the cast → nil → UI showed 1.0x everywhere and a save from that state wiped real values. The ObjC pipeline never noticed because [str floatValue] tolerates both. This single asymmetry retroactively explains multiple 'settings reverted/gone' reports attributed earlier to scope splits. Fixes: (1) type-tolerant reads in both Swift readers (accept Double/String/NSNumber), (2) repaired data in place as NSNumber, (3) all future script writes must use ObjC/CFPreferences with @() literals, never Command line interface to a user's defaults.
Syntax:

'defaults' [-verbose] [-currentHost | -host <hostname>] [-container <container identifier>] followed by one of the following:

  read                                 shows all defaults
  read <domain>                        shows defaults for given domain
  read <domain> <key>                  shows defaults for given domain, key

  read-type <domain> <key>             shows the type for the given domain, key

  write <domain> <domain_rep>          writes domain (overwrites existing)
  write <domain> <key> <value>         writes key for domain

  rename <domain> <old_key> <new_key>  renames old_key to new_key

  delete <domain>                      deletes domain
  delete <domain> <key>                deletes key in domain
  delete-all <domain>                  deletes the domain from all containers
  delete-all <domain> Key>             deletes key in domain from all containers

  import <domain> <path to plist>      writes the plist at path to domain
  import <domain> -                    writes a plist from stdin to domain
  export <domain> <path to plist>      saves domain as a binary plist to path
  export <domain> -                    writes domain as an xml plist to stdout
  domains                              lists all domains
  find <word>                          lists all entries containing word
  help                                 print this help

<domain> is ( <domain_name> | -app <application_name> | -globalDomain )
         or a path to a file omitting the '.plist' extension

<value> is one of:
  <value_rep>
  -string <string_value>
  -data <hex_digits>
  -int[eger] <integer_value>
  -float  <floating-point_value>
  -bool[ean] (true | false | yes | no)
  -date <date_rep>
  -array <value1> <value2> ...
  -array-add <value1> <value2> ...
  -dict <key1> <value1> <key2> <value2> ...
  -dict-add <key1> <value1> ... for numeric dicts. Also found /Applications/MousecapeHelper.app — a stray June 10 (v1.3.1) copy of the Helper at a standalone path, potential confusion source for launchd/login-item resolution; candidates for deletion.

## Root cause / fix

When a CFPreferences dict is read with , any NSString value (from defaults CLI or scripts) makes the ENTIRE cast return nil — not just that key. Always type-tolerate: cast to [String: Any] and per-key convert. Verify value classes with a typecheck probe when GUI shows defaults but CLI reads data fine. Never write numeric preference dicts via Command line interface to a user's defaults.
Syntax:

'defaults' [-verbose] [-currentHost | -host <hostname>] [-container <container identifier>] followed by one of the following:

  read                                 shows all defaults
  read <domain>                        shows defaults for given domain
  read <domain> <key>                  shows defaults for given domain, key

  read-type <domain> <key>             shows the type for the given domain, key

  write <domain> <domain_rep>          writes domain (overwrites existing)
  write <domain> <key> <value>         writes key for domain

  rename <domain> <old_key> <new_key>  renames old_key to new_key

  delete <domain>                      deletes domain
  delete <domain> <key>                deletes key in domain
  delete-all <domain>                  deletes the domain from all containers
  delete-all <domain> Key>             deletes key in domain from all containers

  import <domain> <path to plist>      writes the plist at path to domain
  import <domain> -                    writes a plist from stdin to domain
  export <domain> <path to plist>      saves domain as a binary plist to path
  export <domain> -                    writes domain as an xml plist to stdout
  domains                              lists all domains
  find <word>                          lists all entries containing word
  help                                 print this help

<domain> is ( <domain_name> | -app <application_name> | -globalDomain )
         or a path to a file omitting the '.plist' extension

<value> is one of:
  <value_rep>
  -string <string_value>
  -data <hex_digits>
  -int[eger] <integer_value>
  -float  <floating-point_value>
  -bool[ean] (true | false | yes | no)
  -date <date_rep>
  -array <value1> <value2> ...
  -array-add <value1> <value2> ...
  -dict <key1> <value1> <key2> <value2> ...
  -dict-add <key1> <value1> ....
