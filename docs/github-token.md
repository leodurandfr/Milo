# GitHub Token Setup

This guide explains how to configure a GitHub token to increase API request limits and enable more frequent automatic dependency updates.

## Why is this recommended?

Milō automatically checks for updates to external dependencies (go-librespot, snapcast, etc.) via the GitHub API.

**Without token:**
- ❌ Limit: 60 requests/hour
- ❌ Risk of rate limiting with frequent checks

**With token:**
- ✅ Limit: 5000 requests/hour
- ✅ No risk of rate limiting

## Setup steps

### 1. Create a GitHub token

1. Log in to your GitHub account
2. Go to https://github.com/settings/tokens
3. Click **"Generate new token (classic)"**
4. Give the token a name (e.g., "Milo Dependency Updates")
5. **Select scope**: `public_repo` (read-only access to public repositories)
   - ⚠️ **No other permissions needed** - `public_repo` is sufficient
6. Click **"Generate token"**
7. **Copy the token immediately** (it won't be visible again)

### 2. Add the token to the backend service

#### Open the systemd configuration file:

```bash
sudo nano /etc/systemd/system/milo-backend.service
```

#### Find the Environment line:

You'll see this line:
```ini
Environment="GITHUB_TOKEN=YOUR_GITHUB_TOKEN_HERE"
```

#### Replace the placeholder:

Replace `YOUR_GITHUB_TOKEN_HERE` with your actual token:
```ini
Environment="GITHUB_TOKEN=ghp_YourActualTokenHere123456789"
```

**Complete example:**
```ini
[Unit]
Description=Milo Backend Service
After=network.target

[Service]
Type=simple
User=milo
Group=milo
WorkingDirectory=/home/milo/milo
ExecStart=/home/milo/milo/venv/bin/python3 backend/main.py

# GitHub Token for dependency updates
Environment="GITHUB_TOKEN=ghp_1234567890abcdefghijklmnopqrstuvwxyz"

Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

### 3. Reload and restart the service

```bash
sudo systemctl daemon-reload
sudo systemctl restart milo-backend
```

### 4. Verify the token is detected

Check the backend logs:

```bash
sudo journalctl -u milo-backend -n 50 | grep "GitHub token"
```

**You should see:**
```
GitHub token detected - using authenticated API (5000 req/hour)
```

**If the token is not detected:**
```
No GitHub token found - using anonymous API (60 req/hour)
```

## Security

### Is the token secure?

✅ **Yes, if you follow these best practices:**

1. **Minimal permissions**: Only `public_repo` is needed (read-only)
2. **Local storage**: Token stored only on your Raspberry Pi
3. **Restricted access**: Systemd file readable only by root and milo user
4. **No commits**: Token is never committed to git

### What can someone do with this token?

With `public_repo` scope only:
- ✅ Read public GitHub repositories (releases, tags, commits)
- ❌ Modify anything on your account
- ❌ Access your private repositories
- ❌ Create/delete repositories

### Token revocation

If you think your token has been compromised:

1. Go to https://github.com/settings/tokens
2. Find the token in the list
3. Click **"Delete"**
4. Create a new token and repeat the setup

## Troubleshooting

### Token doesn't work

**Check the syntax:**
```bash
sudo systemctl cat milo-backend.service | grep GITHUB_TOKEN
```

The line should be:
```ini
Environment="GITHUB_TOKEN=ghp_..."
```

**Common errors:**
- ❌ Missing quotes: `Environment=GITHUB_TOKEN=ghp_...`
- ❌ Spaces around =: `Environment = "GITHUB_TOKEN=..."`
- ❌ Truncated or incorrectly copied token

### Check token usage

You can check your current rate limit with curl:

```bash
curl -H "Authorization: token YOUR_TOKEN_HERE" https://api.github.com/rate_limit
```

Expected result:
```json
{
  "resources": {
    "core": {
      "limit": 5000,
      "remaining": 4998,
      "reset": 1234567890
    }
  }
}
```

### Backend won't restart after modification

**Check for errors:**
```bash
sudo systemctl status milo-backend
```

**Test systemd configuration:**
```bash
sudo systemd-analyze verify milo-backend.service
```

## Alternative: System environment variable

If you prefer not to modify the systemd file, you can set a system environment variable:

### Option 1: In `/etc/environment`

```bash
sudo nano /etc/environment
```

Add:
```
GITHUB_TOKEN=ghp_YourTokenHere
```

Restart the system:
```bash
sudo reboot
```

### Option 2: In milo user profile

```bash
sudo nano /home/milo/.bashrc
```

Add at the end:
```bash
export GITHUB_TOKEN=ghp_YourTokenHere
```

Reload:
```bash
sudo systemctl restart milo-backend
```

## FAQ

### Do I need to renew the token regularly?

GitHub Classic tokens don't expire automatically, but you can set an expiration date when creating them.

**Recommendation:** Set a 1-year expiration and renew annually.

### Can I use the same token for multiple Milō instances?

✅ Yes! A single token can be used on multiple Raspberry Pis (main Milō + satellites).

### Does the token affect performance?

❌ No, the token is only used for update checks (1-2 times per day maximum).

### What happens if I don't configure a token?

Milō will work normally, but:
- ⚠️ Update checks limited (60/hour)
- ⚠️ Risk of rate limiting if multiple Milō instances on the same network

## Resources

- [GitHub Tokens Documentation](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/creating-a-personal-access-token)
- [GitHub API Rate Limiting](https://docs.github.com/en/rest/overview/resources-in-the-rest-api#rate-limiting)
- [Back to main documentation](../README.md)
