import java.util.*;
class Main{
    public static void main(String[] args){
        Scanner sc = new Scanner(System.in);
        int n=sc.nextInt();
        int[][] arr = new int[n][n];
        
        for(int i=0;i<n;i++){
            for(int j=0;j<n;j++){
                arr[i][j]=sc.nextInt();
            }
        }
        
        for(int j=0;j<n;j++){
            for(int i=0;i<n;i++){
                System.out.print(arr[i][j]+" ");
            }
            System.out.println();
        }
        
        int maxsum = 0;
        int secondarysum = 0;
        for(int i=0;i<n;i++){
            for(int j=0;j<n;j++){
                maxsum+=arr[i][j];
                secondarysum=arr[i][n - 1 - i];
            }
        }
        System.out.println("Main Sum: "+maxsum);
        System.out.println("Secondary Sum: "+secondarysum);
        System.out.println("Total Sum: "+(maxsum + secondarysum));
    }
}