import { useState, useEffect } from 'react'
import Carousel from '@/components/ui/Carousel'
import Card from '@/components/ui/Card'
import type { CarouselApi } from '@/components/ui/Carousel'

const WithApi = () => {
    const [api, setApi] = useState<CarouselApi>()
    const [current, setCurrent] = useState(0)
    const [count, setCount] = useState(0)

    useEffect(() => {
        if (!api) return

        setCount(api.scrollSnapCount)
        setCurrent(api.selectedIndex + 1)
    }, [api])

    useEffect(() => {
        if (api && api.selectedIndex + 1 !== current) {
            setCurrent(api.selectedIndex + 1)
        }
    }, [api, current])

    return (
        <div className="w-full max-w-xs mx-auto">
            <div className="relative">
                <Carousel setApi={setApi}>
                    <Carousel.Content>
                        {Array.from({ length: 5 }).map((_, index) => (
                            <Carousel.Item key={index}>
                                <Card className="p-1">
                                    <div className="flex aspect-square items-center justify-center p-6">
                                        <span className="text-4xl font-semibold">
                                            {index + 1}
                                        </span>
                                    </div>
                                </Card>
                            </Carousel.Item>
                        ))}
                    </Carousel.Content>
                    <Carousel.Previous className="absolute -left-12 top-1/2 -translate-y-1/2" />
                    <Carousel.Next className="absolute -right-12 top-1/2 -translate-y-1/2" />
                </Carousel>
            </div>
            <div className="py-2 text-center text-sm text-gray-500">
                Slide {current} of {count}
            </div>
        </div>
    )
}

export default WithApi
