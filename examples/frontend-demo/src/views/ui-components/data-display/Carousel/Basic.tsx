import Carousel from '@/components/ui/Carousel'
import Card from '@/components/ui/Card'

const Basic = () => {
    return (
        <div className="w-full max-w-xs mx-auto">
            <div className="relative">
                <Carousel>
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
        </div>
    )
}

export default Basic
