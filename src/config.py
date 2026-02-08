#!/usr/bin/env python3
"""
Centralized configuration for app mappings and categories.
Edit this file to customize app names and categories.
"""

# Bundle ID → Display Name (for Mac data from knowledgeC.db)
APP_MAP = {
    # Social
    "com.hammerandchisel.discord": "Discord",
    "com.hnc.Discord": "Discord",
    "com.atebits.Tweetie2": "X",
    "com.burbn.instagram": "Instagram",
    "com.toyopagroup.picaboo": "Snapchat",
    "com.zhiliaoapp.musically": "TikTok",
    "com.reddit.Reddit": "Reddit",
    "net.whatsapp.WhatsApp": "WhatsApp",
    "net.whatsapp.WhatsAppSMB": "WhatsApp Business",

    # Communication
    "com.apple.MobileSMS": "Messages",
    "com.apple.mobilephone": "Phone",
    "com.apple.InCallService": "Phone Call",
    "com.microsoft.skype.teams": "Microsoft Teams",
    "com.google.Gmail": "Gmail",
    "de.web.mobilenavigator": "WEB.DE Mail",
    "com.microsoft.Office.Outlook": "Outlook",

    # Productivity
    "com.apple.dt.Xcode": "Xcode",
    "com.microsoft.VSCode": "VS Code",
    "com.apple.Terminal": "Terminal",
    "com.apple.finder": "Finder",
    "com.google.antigravity": "Antigravity",
    "com.personio": "Personio",
    "com.github.stormbreaker.prod": "GitHub",

    # Browser
    "com.google.Chrome": "Chrome",
    "com.google.chrome.ios": "Chrome",
    "com.apple.Safari": "Safari",

    # Media
    "com.spotify.client": "Spotify",
    "com.apple.mobileslideshow": "Photos",
    "com.apple.camera": "Camera",

    # Utilities
    "com.google.gemini": "Google Gemini",
    "io.robbie.HomeAssistant": "Home Assistant",
    "com.microsoft.azureauthenticator": "MS Authenticator",
    "com.apple.mobiletimer": "Clock",
    "com.apple.mobilecal": "Calendar",
    "com.apple.weather": "Weather",
    "com.apple.Preferences": "Settings",
    "com.apple.systempreferences": "System Settings",
    "com.apple.AppStore": "App Store",
    "com.ubnt.unifiac": "UniFi",

    # Shopping
    "com.amazon.AmazonDE": "Amazon",
    "de.deutschepost.dhl": "DHL",
    "com.ebaykleinanzeigen.ebc": "Kleinanzeigen",
    "com.6minutesmedia.mydealz": "Mydealz",
    "com.lidl.eci.lidl.plus": "Lidl Plus",

    # System (often ignorable)
    "com.apple.springboard.home-screen-open-folder": "System: Folder",
    "com.apple.springboard.today-view": "System: Today View",
    "com.apple.control-center": "Control Center",
    "com.apple.SleepLockScreen": "Lock Screen",
    "com.apple.ClockAngel": "Clock Widget",
    "com.apple.iphonesimulator": "iPhone Simulator",

    # Other
    "nichtlegacy.your-spotify": "Your_Spotify",
}

# Long App Store Name → Short Name (for iPhone data)
TITLE_NORMALIZE = {
    # Social
    "WhatsApp Messenger": "WhatsApp",
    "TikTok - Videos, Shopping & mehr": "TikTok",
    "TikTok - Videos, Shop": "TikTok",
    "TikTok - Videos": "TikTok",
    "Discord - Talk, Play, Hang Out": "Discord",
    "Discord - Talk, Play, H": "Discord",
    "Discord - Talk, Chat & Hang Out": "Discord",
    "Instagram": "Instagram",
    "Snapchat": "Snapchat",
    "X": "X",
    "X (Twitter)": "X",
    "LinkedIn: Network & Job Finder": "LinkedIn",
    "Telegram Messenger": "Telegram",

    # Communication
    "Gmail - Email by Google": "Gmail",
    "Microsoft Outlook": "Outlook",
    "WEB.DE - Mail, Cloud & News": "WEB.DE",
    "WEB.DE - Mail": "WEB.DE",
    "GMX - Mail & Cloud": "GMX",

    # Browser
    "Google Chrome": "Chrome",

    # Media
    "Spotify: Music and Podcasts": "Spotify",
    "stats.fm for Spotify Music App": "stats.fm",
    "Radio Germany Online - Live Internet FM & Webradio": "Radio Germany",
    "Plex: Stream Live TV Channels": "Plex",
    "Plex: Stream Live TV Ch": "Plex",
    "Plex Dash": "Plex",
    "PlexOVision": "Plex",

    # Shopping
    "AliExpress - Shopping App": "AliExpress",
    "Amazon Business: B2B Shopping": "Amazon Business",
    "Kleinanzeigen - without eBay": "Kleinanzeigen",
    "Kleinanzeigen - ohne eBay": "Kleinanzeigen",
    "eBay online shopping & selling": "eBay",
    "Klarna | Pay your way": "Klarna",
    "Lieferando.de": "Lieferando",
    "Too Good To Go: End Food Waste": "Too Good To Go",

    # Finance
    "PayPal - Pay, Send": "PayPal",
    "PayPal - Pay": "PayPal",
    "Revolut: Send, spend and save": "Revolut",
    "Revolut: Send": "Revolut",
    "N26 — Love your bank": "N26",
    "Finanzguru - Konten & Verträge": "Finanzguru",
    "Crypto Pro: Live Coin Tracker": "Crypto Pro",
    "Moss by Nufin": "Moss",
    "Paycell - Digital Wallet": "Paycell",

    # Utilities
    "Microsoft Authenticator": "Microsoft Authenticator",
    "Speedtest by Ookla": "Speedtest",
    "Proton VPN: Fast & Secure": "ProtonVPN",
    "Proxyman - Capture HTTPS": "Proxyman",
    "Weather & Radar - Storm radar": "Weather",
    "Cowboy - Electric Bikes": "Cowboy",
    "Health Auto Export - JSON+CSV": "Health Auto Export",
    "Bevel: All-In-One Health App": "Bevel",
    "Ubiquiti WiFiman": "WiFiman",

    # Productivity
    "Microsoft SwiftKey AI Keyboard": "SwiftKey",
    "Intune Company Portal": "Intune",
}

# App Title → Category
CATEGORIES = {
    # Social
    "Discord": "Social",
    "X": "Social",
    "Instagram": "Social",
    "Snapchat": "Social",
    "TikTok": "Social",
    "Reddit": "Social",
    "WhatsApp": "Social",
    "WhatsApp Business": "Social",
    "Facebook": "Social",
    "Threads": "Social",
    "Pinterest": "Social",
    "LinkedIn": "Social",
    "Telegram": "Social",

    # Communication
    "Messages": "Communication",
    "Phone": "Communication",
    "Phone Call": "Communication",
    "Microsoft Teams": "Communication",
    "Gmail": "Communication",
    "Outlook": "Communication",
    "Mail": "Communication",
    "WEB.DE": "Communication",
    "GMX": "Communication",
    "Contacts": "Communication",

    # Productivity
    "Xcode": "Productivity",
    "VS Code": "Productivity",
    "Terminal": "Productivity",
    "Finder": "Productivity",
    "Antigravity": "Productivity",
    "Antigravity-Tools": "Productivity",
    "GitHub": "Productivity",
    "Personio": "Productivity",
    "iPhone Simulator": "Productivity",
    "Instruments": "Productivity",
    "Apple Configurator": "Productivity",
    "TestFlight": "Productivity",
    "Microsoft 365 Admin": "Productivity",
    "Microsoft 365 Copilot": "Productivity",
    "Microsoft OneNote": "Productivity",
    "Microsoft To Do": "Productivity",
    "Excel": "Productivity",
    "Notes": "Productivity",
    "Reminders": "Productivity",
    "Files": "Productivity",
    "Calendar": "Productivity",
    "Calculator": "Productivity",
    "Translate": "Productivity",
    "Preview": "Productivity",
    "Activity Monitor": "Productivity",
    "Python": "Productivity",

    # Browser
    "Chrome": "Browser",
    "Safari": "Browser",
    "ChatGPT": "Browser",
    "Google Maps": "Browser",
    "Google News": "Browser",
    "Google Drive": "Browser",

    # Media
    "Spotify": "Media",
    "Photos": "Media",
    "Camera": "Media",
    "Your_Spotify": "Media",
    "stats.fm": "Media",
    "YouTube": "Media",
    "Music": "Media",
    "Infuse": "Media",
    "Plex": "Media",
    "Letterboxd": "Media",
    "ILOVEMUSIC.DE": "Media",
    "Radio Germany": "Media",
    "PlayStation App": "Media",

    # Utilities
    "Google Gemini": "Utilities",
    "Home Assistant": "Utilities",
    "Microsoft Authenticator": "Utilities",
    "Bitwarden Authenticator": "Utilities",
    "Bitwarden Password Manager": "Utilities",
    "Clock": "Utilities",
    "Weather": "Utilities",
    "Settings": "Utilities",
    "System Settings": "Utilities",
    "App Store": "Utilities",
    "UniFi": "Utilities",
    "Ubiquiti WiFiman": "Utilities",
    "Find My": "Utilities",
    "Home": "Utilities",
    "Passwords": "Utilities",
    "Shortcuts": "Utilities",
    "Speedtest": "Utilities",
    "Tailscale": "Utilities",
    "WireGuard": "Utilities",
    "ProtonVPN": "Utilities",
    "Proxyman": "Utilities",
    "ECOVACS HOME": "Utilities",
    "Cowboy": "Utilities",
    "Untis Mobile": "Utilities",
    "Apple Fitness": "Utilities",
    "Apple Health": "Utilities",
    "Health Auto Export": "Utilities",
    "RENPHO Health": "Utilities",
    "Bevel": "Utilities",

    # Shopping
    "Amazon": "Shopping",
    "Amazon Business": "Shopping",
    "DHL": "Shopping",
    "Kleinanzeigen": "Shopping",
    "Mydealz": "Shopping",
    "Lidl Plus": "Shopping",
    "AliExpress": "Shopping",
    "eBay": "Shopping",
    "Klarna": "Shopping",
    "Lieferando": "Shopping",
    "Too Good To Go": "Shopping",
    "Heritage Auctions": "Shopping",

    # Finance
    "PayPal": "Finance",
    "Revolut": "Finance",
    "N26": "Finance",
    "Finanzguru": "Finance",
    "Crypto Pro": "Finance",
    "Moss": "Finance",
    "Paycell": "Finance",
    "comdirect photoTAN App": "Finance",
}


def normalize_title(title: str) -> str:
    """Normalizes app titles to short, consistent names."""
    # Exact match
    if title in TITLE_NORMALIZE:
        return TITLE_NORMALIZE[title]
    # Partial match for truncated titles
    for long_name, short_name in TITLE_NORMALIZE.items():
        if title.startswith(long_name[:15]):
            return short_name
    return title


def get_category(title: str) -> str:
    """Determines the category of an app."""
    if title in CATEGORIES:
        return CATEGORIES[title]

    # Detect system apps
    if title.startswith("System:") or title in ["Lock Screen", "Control Center", "Clock Widget"]:
        return "System"

    return "Other"
