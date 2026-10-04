- **The full notebook worker image no longer needs the Launchpad API to build.**
  Its amd64 base still added the `ppa:dotnet/backports` apt repository, a
  leftover from before .NET was installed with `dotnet-install.sh`; nothing
  installed from it. `add-apt-repository` looks up the PPA's signing key through
  `api.launchpad.net`, so the build failed (`OSError: [Errno 101] Network is
  unreachable`) on any network where Docker could not reach that host. The
  unused repository is gone.
