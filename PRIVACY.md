Viper IDE privacy statement
===========================

Viper IDE has no telemetry, analytics or crash reporting, and never sends anything to its
developers. It only connects to other systems for the features below.

Automatic (on by default, can be turned off)
--------------------------------------------
- Missing package lookups: when an open file imports a module that isn't installed, or a run
  fails with ModuleNotFoundError, Viper asks pypi.org whether a package with that name exists.
  Only the package name is sent (plus what any web request carries, such as your IP address).
  Turn this off in Settings: untick "Detect imports that aren't installed" and set installs to
  "Never offer".
- Update checks: Viper reads version.json from the update servers listed in Settings > Update
  server URLs. The builds published on GitHub have no update server built in, so they make no
  update requests unless you add one. "Check for Viper updates at startup" turns it off.

Only when you ask for it
------------------------
- Installing packages and formatters runs pip, which downloads from pypi.org (or the package
  index your pip is configured to use).
- Download Python fetches the version list from endoflife.date and the installer from
  python.org (or the NuGet package from nuget.org), and pip's installer from bootstrap.pypa.io.
- The AI Assistant sends your message, and the file, errors and panel output you choose to
  include, to the AI provider you configure (for example OpenAI, OpenRouter, Google Gemini,
  Groq, Mistral, DeepSeek, or a server on your own machine). Your API key is only sent to its
  own provider.
- The GitHub Copilot provider signs in through github.com and sends chats to GitHub Copilot
  (api.githubcopilot.com).
- The Microsoft Copilot window loads copilot.microsoft.com and sends what you type there.

Those services handle your data under their own privacy policies, for example
https://policies.python.org/pypi.org/Privacy-Notice/,
https://www.python.org/privacy/,
https://docs.github.com/site-policy/privacy-policies/github-general-privacy-statement and
https://privacy.microsoft.com/privacystatement, and the policy of whichever AI provider you
configure.

Settings, API keys and downloaded tools stay on your computer, in %APPDATA%\ViperIDE and
%LOCALAPPDATA%\ViperIDE (or the Data folder beside ViperIDE.exe for the portable version).
Uninstalling Viper from Windows Settings > Apps removes the program.
