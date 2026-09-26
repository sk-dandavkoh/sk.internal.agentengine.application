import Carousel from '@/components/ui/Carousel'
import Card from '@/components/ui/Card'

const Positioning = () => {
    return (
        <div className="space-y-8">
            <div>
                <h3 className="text-lg font-semibold mb-4">
                    Outside positioning (default)
                </h3>
                <div className="w-full max-w-xs mx-auto">
                    <div className="relative">
                        <Carousel>
                            <Carousel.Content>
                                {Array.from({ length: 3 }).map((_, index) => (
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
                </div>
            </div>
            <div>
                <h3 className="text-lg font-semibold mb-4">
                    Inside positioning
                </h3>
                <div className="w-full max-w-xs mx-auto">
                    <div className="relative">
                        <Carousel>
                            <Carousel.Content>
                                {Array.from({ length: 3 }).map((_, index) => (
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
                            <Carousel.Previous className="absolute left-2 top-1/2 -translate-y-1/2 z-10" />
                            <Carousel.Next className="absolute right-2 top-1/2 -translate-y-1/2 z-10" />
                        </Carousel>
                    </div>
                </div>
            </div>
            <div>
                <h3 className="text-lg font-semibold mb-4">
                    Bottom positioning
                </h3>
                <div className="w-full max-w-xs mx-auto">
                    <div className="relative">
                        <Carousel>
                            <Carousel.Content>
                                {Array.from({ length: 3 }).map((_, index) => (
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
                            <div className="flex justify-center gap-2 mt-4">
                                <Carousel.Previous />
                                <Carousel.Next />
                            </div>
                        </Carousel>
                    </div>
                </div>
            </div>
            <div>
                <h3 className="text-lg font-semibold mb-4">Custom styling</h3>
                <div className="w-full max-w-xs mx-auto">
                    <div className="relative">
                        <Carousel>
                            <Carousel.Content>
                                {Array.from({ length: 3 }).map((_, index) => (
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
                            <Carousel.Previous
                                className="absolute -left-12 top-1/2 -translate-y-1/2 bg-blue-500 hover:bg-blue-600 text-white border-blue-500"
                                variant="solid"
                            />
                            <Carousel.Next
                                className="absolute -right-12 top-1/2 -translate-y-1/2 bg-blue-500 hover:bg-blue-600 text-white border-blue-500"
                                variant="solid"
                            />
                        </Carousel>
                    </div>
                </div>
            </div>
        </div>
    )
}

export default Positioning
