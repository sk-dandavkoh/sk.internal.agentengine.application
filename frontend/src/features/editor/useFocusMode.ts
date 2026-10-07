import { useEffect, useState } from 'react'

/**
 * The editor's full-screen mode: the page lays the editor over the app
 * (works everywhere), and asks the browser for real full screen too. Leaving
 * the browser's full screen (Escape) ends the mode; ending the mode leaves
 * it. Dialogs and toasts stay visible: the whole document is full screen,
 * not one element.
 */
export function useFocusMode() {
    const [on, setOn] = useState(false)
    useEffect(() => {
        if (!on) return
        const root = document.documentElement
        try {
            void root.requestFullscreen().catch(() => {
                // Refused (an iframe, a policy, no user gesture): the
                // in-page mode alone still gives the editor the screen.
            })
        } catch {
            // No Fullscreen API at all (older browsers, jsdom): same.
        }
        const onChange = () => {
            if (!document.fullscreenElement) setOn(false)
        }
        document.addEventListener('fullscreenchange', onChange)
        return () => {
            document.removeEventListener('fullscreenchange', onChange)
            if (document.fullscreenElement) void document.exitFullscreen()
        }
    }, [on])
    return [on, setOn] as const
}
