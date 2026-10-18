' Made by make-tv-app.sh on the Mac: where the board is, and the TV key. Not the board password.
' The TV tries the home Wi-Fi address first, then the ts.net link.
function BoardConfig() as object
    return {
        home: "http://MAC_ADDRESS:8090/api/tv?key=TV_KEY",
        away: "TS_NET_URL/api/tv?key=TV_KEY"
    }
end function
