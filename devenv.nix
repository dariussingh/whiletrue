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

  languages.javascript = {
    enable = true;
    package = pkgs.nodejs_24;
    pnpm = {
      enable = true;
      install.enable = true;
    };
  };

  scripts.check.exec = "pnpm check";

  enterShell = ''
    echo "whiletrue devenv: node $(node -v), pnpm $(pnpm -v)"
  '';
}
