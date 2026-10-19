{
  pkgs,
  ...
}:
{
  packages = with pkgs; [
    git
    gh
    tmux
  ];

  languages.python = {
    enable = true;
    version = "3.12";
    uv = {
      enable = true;
      sync.enable = true;
      sync.allGroups = true;
    };
  };

  scripts.check.exec = "uv run poe check";

  enterShell = ''
    . .devenv/state/venv/bin/activate
    echo "whiletrue devenv: $(python --version), uv $(uv --version | cut -d' ' -f2)"
  '';
}
