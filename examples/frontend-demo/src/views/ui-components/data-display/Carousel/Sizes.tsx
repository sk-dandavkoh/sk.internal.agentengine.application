import Carousel from '@/components/ui/Carousel'
import Card from '@/components/ui/Card'

const Sizes = () => {
    return (
        <div className="w-full max-w-sm mx-auto">
            <div className="relative">
                <Carousel>
                    <Carousel.Content className="-ml-2 md:-ml-4">
                        {Array.from({ length: 5 }).map((_, index) => (
                            <Carousel.Item
                                key={index}
                                className="pl-2 md:pl-4 basis-1/2 md:basis-1/3"
                            >
                                <Card>
                                    <div className="flex aspect-square items-center justify-center p-6">
                                        <span className="text-3xl font-semibold">
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
        </div>
    )
}

export default Sizes
