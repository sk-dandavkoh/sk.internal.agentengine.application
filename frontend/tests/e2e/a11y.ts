import AxeBuilder from '@axe-core/playwright'
import type { Page } from '@playwright/test'
import { expect } from './fixtures'

// WCAG 2.2 A/AA plus axe's best practices: the bar Phase 25 audits against.
const TAGS = [
    'wcag2a',
    'wcag2aa',
    'wcag21a',
    'wcag21aa',
    'wcag22aa',
    'best-practice',
]

/** No automatically detectable accessibility violation on the page as it is
 * now. Axe finds roughly a third of real issues: keyboard and screen-reader
 * behavior needs its own tests. */
export async function expectAccessible(page: Page) {
    const { violations } = await new AxeBuilder({ page })
        .withTags(TAGS)
        .analyze()
    expect(
        violations.map(
            (v) =>
                `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`,
        ),
    ).toEqual([])
}

/** The page never scrolls sideways; wide content scrolls in its own box. */
export async function expectNoHorizontalScroll(page: Page) {
    // A string: the specs compile without DOM types.
    const overflow = await page.evaluate<boolean>(
        'document.documentElement.scrollWidth > document.documentElement.clientWidth',
    )
    expect(overflow).toBe(false)
}
