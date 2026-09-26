import DemoLayout from '@/components/docs/DemoLayout'

// Demo
import Basic from './Basic'
import Vertical from './Vertical'
import WithApi from './WithApi'
import Sizes from './Sizes'
import Positioning from './Positioning'

const mdPath = 'Carousel'

const demoHeader = {
    title: 'Carousel',
    desc: 'A carousel with motion and swipe built without external dependencies.',
}

const demos = [
    {
        mdName: 'Basic',
        mdPath: mdPath,
        title: 'Basic',
        desc: `Basic usage of Carousel with navigation buttons.`,
        component: <Basic />,
    },
    {
        mdName: 'Vertical',
        mdPath: mdPath,
        title: 'Vertical',
        desc: `Set <code>orientation="vertical"</code> to display a vertical carousel.`,
        component: <Vertical />,
    },
    {
        mdName: 'WithApi',
        mdPath: mdPath,
        title: 'With API',
        desc: `Use <code>setApi</code> prop to access the carousel API for programmatic control.`,
        component: <WithApi />,
    },
    {
        mdName: 'Sizes',
        mdPath: mdPath,
        title: 'Sizes',
        desc: `Customize item sizes using <code>basis-*</code> classes on CarouselItem.`,
        component: <Sizes />,
    },
    {
        mdName: 'Positioning',
        mdPath: mdPath,
        title: 'Button Positioning',
        desc: `Control navigation button placement with custom CSS classes. Buttons no longer have built-in positioning.`,
        component: <Positioning />,
    },
]

const demoApi = [
    {
        component: 'Carousel',
        api: [
            {
                propName: 'orientation',
                type: `<code>'horizontal' | 'vertical'</code>`,
                default: `<code>'horizontal'</code>`,
                desc: 'The orientation of the carousel',
            },
            {
                propName: 'opts',
                type: `<code>{ startIndex?: number }</code>`,
                default: `<code>{}</code>`,
                desc: 'Carousel options',
            },
            {
                propName: 'setApi',
                type: `<code>(api: CarouselApi) => void</code>`,
                default: `-`,
                desc: 'Callback to receive the carousel API',
            },
        ],
    },
    {
        component: 'Carousel.Content',
        api: [
            {
                propName: 'className',
                type: `<code>string</code>`,
                default: `-`,
                desc: 'Additional CSS classes for the content container',
            },
        ],
    },
    {
        component: 'Carousel.Item',
        api: [
            {
                propName: 'className',
                type: `<code>string</code>`,
                default: `-`,
                desc: 'Additional CSS classes for the item (use basis-* for sizing)',
            },
        ],
    },
    {
        component: 'Carousel.Previous / Carousel.Next',
        api: [
            {
                propName: 'className',
                type: `<code>string</code>`,
                default: `-`,
                desc: 'CSS classes for positioning and styling (no built-in positioning)',
            },
            {
                propName: 'variant',
                type: `<code>'solid' | 'subtle' | 'default' | 'ghost' | 'link'</code>`,
                default: `<code>'default'</code>`,
                desc: 'Button variant style',
            },
            {
                propName: 'size',
                type: `<code>'sm' | 'md' | 'lg'</code>`,
                default: `<code>'sm'</code>`,
                desc: 'Button size',
            },
        ],
    },
]

const CarouselDemo = () => {
    return <DemoLayout header={demoHeader} demos={demos} api={demoApi} />
}

export default CarouselDemo
